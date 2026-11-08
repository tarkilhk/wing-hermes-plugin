# Credits and origins

Wing is a companion plugin that adds GPT Live voice and email draft cards to the
Wing Android application. Its two features draw on the projects credited below.
Wing additions are copyright 2026 Wing contributors, under the MIT license.

## GPT Live voice

Wing's initial subscription bridge was developed alongside
[Synero/hermes-live-voice](https://github.com/Synero/hermes-live-voice), based on
commit `8bcddd5`. That project is MIT-licensed, copyright 2026 Synero.

The voice integration builds on that subscription transport work and reuses
Hermes's signed-provider request API and voice instruction builder, loaded from
the user's installed Hermes. It does not bundle the original project's desktop
interface or its vendored hermes-talk implementation.

## Email draft cards

Email drafts are adapted from the structured draft/version design and standalone
dashboard loading pattern of [arsiktech/hermes-draft-cards](https://github.com/arsiktech/hermes-draft-cards),
commit `6e6ca659ece59b0ce1c28b3b188393491ee5a415` (MIT, copyright 2026 arsiktech).
Wing uses a separate immutable email-only contract and native Android renderer;
it does not include that project's desktop UI, approval or sending workflow.

Both original copyright notices and the MIT permission notice are retained in
[LICENSE](LICENSE).
