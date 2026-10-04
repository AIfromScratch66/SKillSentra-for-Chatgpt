# Third-party notices

SKillSentra for Chatgpt includes or uses third-party software under the licenses selected by their respective copyright holders. Those licenses apply only to the corresponding third-party components and do not grant a license to this repository's original code.

Runtime and test dependencies are installed from their official package distributions rather than copied into this repository. The dependency inventory generated for each release is published as `sbom.cdx.json`.

Notable development dependencies include:

- Playwright and `playwright-core`, Apache License 2.0.
- `fsevents`, MIT License, when selected by the package manager on supported platforms.

Optional local artwork utilities may use Pillow and NumPy when installed by the operator. Their licenses and notices are provided by their package distributions.

Copyright and license notices shipped with each third-party package remain controlling. Review the release SBOM and the installed package metadata before redistribution.
