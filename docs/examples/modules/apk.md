# Module examples: apk

Scripts under `examples/modules/apk/` — one per fact of the
[`apk`](../../modules/apk.md) module. See the
[Examples overview](../../examples.md) for the shared "connects for real"
warning and the error-reporting contract every script follows.

The read-only fact-gathering examples (`apk_apk_packages.py` reading
`ApkPackages`, `apk_apk_explicit_packages.py` reading `ApkExplicitPackages`,
`apk_apk_repositories.py` reading `ApkRepositories` and `apk_apk_package.py`
reading `ApkPackage` for one package, all over `gather_fact`) are not rendered
here — they read a single fact, and the facts themselves are documented on
the [`apk` module page](../../modules/apk.md).
