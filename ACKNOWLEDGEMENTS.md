# Acknowledgements

GateStream builds on the supplied **OmniArchiver-F2L / OmniArchiver** project.

- Original upstream maintainer: [WorkerOfArea51](https://github.com/WorkerOfArea51).
- Original upstream repository: [OmniArchiver-F2L](https://github.com/WorkerOfArea51/OmniArchiver-F2L).
- GateStream rebrand prepared for [Siam](https://github.com/Chowdhury-Siam).

The rebrand preserves the upstream attribution. No new licensing grant or
ownership claim over upstream code is introduced by this release.

## Go runtime

The Go port uses [gotd/td](https://github.com/gotd/td) for MTProto, the official
MongoDB Go driver, godotenv and the modernc SQLite reader. Amaterasu's
[Go engine](https://github.com/its-niloy/Amaterasu/tree/main/go-engine) supplied
the user's reference for the Go Telegram dependency. Dependency license notices
are bundled under `third_party_licenses/`.
