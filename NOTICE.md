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

Email drafts are adapted from the structured draft/version design and standalone
dashboard loading pattern of [arsiktech/hermes-draft-cards](https://github.com/arsiktech/hermes-draft-cards),
commit `6e6ca659ece59b0ce1c28b3b188393491ee5a415` (MIT, copyright 2026 arsiktech).
Wing uses a separate immutable email-only contract and native Android renderer;
it does not include that project's desktop UI, approval or sending workflow.
The original copyright and MIT permission notice are retained in LICENSE.
