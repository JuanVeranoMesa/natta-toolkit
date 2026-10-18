# Homebrew formula: this repository doubles as its own tap.
#   brew tap juanveranomesa/natta https://github.com/JuanVeranoMesa/natta-toolkit
#   brew install natta
# The checkout is installed under libexec; bin/natta is symlinked, so the launcher
# resolves the installed checkout exactly as it does for a source checkout.
class Natta < Formula
  desc "Local-first deterministic control plane for registered development projects"
  homepage "https://github.com/JuanVeranoMesa/natta-toolkit"
  url "https://github.com/JuanVeranoMesa/natta-toolkit/archive/refs/tags/v1.1.0.tar.gz"
  sha256 "998977428f6e27ad07e57d840c956947525621f8e86de09b9594b4bafd538c33"
  license "MIT"
  head "https://github.com/JuanVeranoMesa/natta-toolkit.git", branch: "main"

  depends_on :macos
  depends_on "python@3.14"

  def install
    # Default the launcher to Homebrew's Python; NATTA_PYTHON still overrides it.
    inreplace "bin/natta", "NATTA_PYTHON=${NATTA_PYTHON:-python3}",
              "NATTA_PYTHON=${NATTA_PYTHON:-#{Formula["python@3.14"].opt_bin}/python3.14}"
    libexec.install "bin", "tools", "examples", "docs", "README.md", "LICENSE"
    bin.install_symlink libexec/"bin/natta"
  end

  def caveats
    <<~EOS
      Register a repository, then check readiness:
        natta project add example ~/Projects/example
        natta doctor
      Semantic inspection additionally needs `natta confinement validate` on this host.
    EOS
  end

  test do
    assert_match "local-first", shell_output("#{bin}/natta --help")
    repo = testpath/"repo"
    repo.mkpath
    system "git", "-C", repo, "init", "-q"
    registry = testpath/"projects.toml"
    system bin/"natta", "--registry", registry, "project", "add", "demo", repo
    assert_match "demo", shell_output("#{bin}/natta --registry #{registry} projects")
  end
end
