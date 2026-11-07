# Origins

Wing's initial subscription bridge was developed alongside
[Synero/hermes-live-voice](https://github.com/Synero/hermes-live-voice), based on
commit `8bcddd5`. That project is MIT-licensed, copyright 2026 Synero.

This standalone package extracts Wing's narrow subscription transport. It does
not bundle the former project's desktop interface or its vendored hermes-talk
implementation. Its design reuses Hermes's signed-provider request API and voice
instruction builder, loaded from the user's installed Hermes.

The Synero copyright notice is retained with the MIT license. Wing additions
are copyright 2026 Wing contributors, under the same license.
