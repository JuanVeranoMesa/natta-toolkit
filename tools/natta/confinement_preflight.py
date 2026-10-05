"""Explicit disposable native-primitive probes. No backend enablement or app access."""
import errno
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

from confinement import detect_host


def main():
    host = detect_host()
    print(json.dumps(host.as_dict(), sort_keys=True))
    if not host.native_primitive_present:
        return 1
    def run(argv):
        return subprocess.run(argv, capture_output=True, text=True, timeout=20)
    smoke = run(['/usr/bin/sandbox-exec','-p','(version 1)(allow default)','/usr/bin/true'])
    if smoke.returncode:
        print('Native application unavailable: ' + smoke.stderr.strip()[:500])
        return 1
    with tempfile.TemporaryDirectory(prefix='natta-confinement-probe-') as temp:
        root = Path(temp).resolve()
        project = root/'project'; project.mkdir(mode=0o700)
        runtime = root/'runtime'; runtime.mkdir(mode=0o700)
        marker = project/'marker';marker.write_text('unchanged')
        profile = root/'profile.sb'
        profile.write_text('(version 1)(allow default)\n(deny file-write* (subpath (param "PROJECT")))\n(deny network*)\n')
        os.chmod(profile,0o600)
        prefix=['/usr/bin/sandbox-exec','-D',f'PROJECT={project}','-f',str(profile)]
        denied = '''import errno,sys
from pathlib import Path
try:
 Path(sys.argv[1]).write_text('forbidden')
except OSError as e:
 sys.exit(0 if e.errno in (errno.EPERM,errno.EACCES) else 2)
sys.exit(1)
'''
        # Controls establish that ordinary OS permissions allow each operation.
        control = run([sys.executable,'-c',"from pathlib import Path;import sys;Path(sys.argv[1]).write_text('unchanged')",str(marker)])
        if control.returncode: return 1
        checks=[]
        checks.append(('project_write_denied',run([*prefix,sys.executable,'-c',denied,str(marker)]).returncode==0 and marker.read_text()=='unchanged'))
        descendant='import subprocess,sys;sys.exit(subprocess.run([sys.executable,"-c",sys.argv[1],sys.argv[2]]).returncode)'
        checks.append(('descendant_write_denied',run([*prefix,sys.executable,'-c',descendant,denied,str(marker)]).returncode==0))
        allowed="from pathlib import Path;import sys;Path(sys.argv[1]).write_text('allowed')"
        checks.append(('fixture_runtime_write_allowed',run([*prefix,sys.executable,'-c',allowed,str(runtime/'output')]).returncode==0))
        profile.write_text('(version 1)(allow default)\n(deny file-read* (subpath (param "PROJECT")))\n')
        reader=denied.replace(".write_text('forbidden')",'.read_text()')
        checks.append(('project_read_denial_supported',run([*prefix,sys.executable,'-c',reader,str(marker)]).returncode==0))
        profile.write_text('(version 1)(allow default)\n(deny network*)\n')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen(4)
            port=str(listener.getsockname()[1])
            connect='import socket,sys;socket.create_connection(("127.0.0.1",int(sys.argv[1])),timeout=2).close()'
            if run([sys.executable,'-c',connect,port]).returncode: return 1
            blocked='''import socket,sys,errno
try:
 socket.create_connection(('127.0.0.1',int(sys.argv[1])),timeout=2)
except OSError as e:
 sys.exit(0 if e.errno in (errno.EPERM,errno.EACCES) else 2)
sys.exit(1)
'''
            checks.append(('network_connection_denied',run([*prefix,sys.executable,'-c',blocked,port]).returncode==0))
        profile.write_text('(version 1)(allow default)\n(deny process-exec (literal "/usr/bin/true"))\n')
        child='''import subprocess,errno,sys
try:
 subprocess.run(['/usr/bin/true'],check=True)
except OSError as e:
 sys.exit(0 if e.errno in (errno.EPERM,errno.EACCES) else 2)
except subprocess.CalledProcessError:
 sys.exit(2)
sys.exit(1)
'''
        checks.append(('process_exec_denial_supported',run([*prefix,sys.executable,'-c',child]).returncode==0))
        for name, passed in checks:print(f'{name}: {"PASS" if passed else "FAIL"}')
        print('Fixture evidence only; production inspection validation is separate. Xcode native confinement is deferred.')
        return 0 if all(passed for _,passed in checks) else 1


if __name__=='__main__':
    raise SystemExit(main())
