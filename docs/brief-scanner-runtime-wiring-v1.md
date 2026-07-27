# Brief Scanner runtime wiring v1

This change connects the disabled-by-default, read-only Brief Scanner path at the existing Groq image boundary.

## Runtime behavior

1. Text-only requests use the existing generic Groq path unchanged.
2. Image requests read the deterministic reply-language marker produced by `build_system_prompt`.
3. When the marker maps to a bounded supported response language, the read-only Brief Scanner application hook is evaluated.
4. A handled, non-empty scanner reply is returned without creating the generic Groq client.
5. Disabled, unsupported, or unhandled scanner outcomes continue through the existing generic vision request unchanged.

## Safety properties

- The Brief Scanner provider feature flag remains disabled by default.
- No mission, reminder, draft, persistence, or telemetry side effect is added.
- No document facts are written to user memory.
- Missing or unknown language metadata fails open to the existing production vision flow rather than guessing a response language.
- Scanner replies still pass through the existing WhatsApp reply sanitizer.
- Generic provider exceptions are logged without third-party exception content.
