# License and third-party provenance

Natta Toolkit source, tests and design contracts are licensed under the
[MIT License](../LICENSE). The current copyright holder is
Juan Desiderio Verano Mesa.

Copyright (c) 2026 Juan Desiderio Verano Mesa

Natta Company is a brand/trade name only, not a separate legal entity, copyright
owner or licensor. Juan Desiderio Verano Mesa is the current rights holder.

Natta Toolkit's MIT License does not relicense third-party dependencies, models,
frameworks, external tools or services. Those components remain owned by their
respective rights holders and retain their own licenses and terms.

No external project source, model weights, templates or copied third-party snippet
was identified in the extracted files. References/imports are not vendoring.
The local-model lock contains upstream package metadata/download hashes, not
packages. Review upstream notices when installing or redistributing dependencies.

| External component | Source/license | Distribution treatment |
| --- | --- | --- |
| OpenJEV | [Pinned source](https://github.com/lookski/openjev/tree/67eedd02d8863dfe37fcd391c55ce2a7b6d82e37), MIT (pinned installed source license and VCS identity inspected) | Optional Git dependency; not vendored; pinned installed license independently confirmed |
| Qwen3-0.6B | [Pinned license](https://huggingface.co/Qwen/Qwen3-0.6B/blob/c1899de289a04d12100db370d81485cdf75e47ca/LICENSE), Apache-2.0 | Reference and explicit downloader only; no weights |
| Qwen3-1.7B | [Pinned source/license](https://huggingface.co/Qwen/Qwen3-1.7B/blob/70d244cc86ccca08cf5af4e1e306ecf908b1ad5e/LICENSE), Apache-2.0 (pinned local license/receipt inspected) | Optional comparison reference; no weights; pinned local license independently confirmed |
| PyTorch | [Upstream license](https://github.com/pytorch/pytorch/blob/main/LICENSE), BSD-style with bundled notices | Optional dependency, not vendored |
| Transformers | [Upstream license](https://github.com/huggingface/transformers/blob/main/LICENSE), Apache-2.0 | Optional dependency, not vendored |
| huggingface-hub | [Upstream license](https://github.com/huggingface/huggingface_hub/blob/main/LICENSE), Apache-2.0 | Optional dependency, not vendored |
| Python / Git / uv / Codex / Xcode | Separately installed tools with their own terms | No tool binaries or authentication redistributed |

The upstream license verification status is recorded in the local review report.
The lock's transitive dependencies remain upstream-owned and retain their respective
licenses. Codex CLI/provider services and Apple/Xcode tools and services remain
subject to their respective terms; Natta Toolkit grants no rights over them.
