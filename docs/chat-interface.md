# Chat interface

The chat composer keeps three kinds of information separate: enabled context above the draft, the writing area, and a compact toolbar below it. The toolbar groups Tools and the configured local model on the left, with voice and send/stop actions on the right. The footer states whether web queries leave the device.

The model picker switches the existing Speed and Balanced presets. It shows their configured Ollama model names without claiming those models are downloaded or ready. Model setup remains in Settings. Voice opens the existing controls and respects the pilot gate.

The welcome screen uses editable starter prompts. Conversations use a restrained user bubble and an open assistant column, with existing code, math, sources, copy, retry and scroll behavior. Theme tokens carry the design into dark mode and compact mini chat.

## Interaction requirements

- Opening the model picker focuses the selected preset. Tab moves between choices; Enter selects. Escape dismisses and returns focus to the trigger. Clicking or tabbing outside dismisses it.
- Response generation locks model and context changes. An interrupted picker stays closed when generation finishes.
- Drafts, IME composition, Enter/Shift+Enter, stop, and mini-chat expansion keep their existing behavior.
- Controls use real capabilities: there are no decorative upload, paid-plan, or unsupported model actions.
- Long model names truncate visually while the complete name remains in the accessible label and tooltip. Narrow layouts retain usable input and toolbar controls.

## Design references

We reviewed the public previews of [AI Prompt Composer with Model Selector by neeraj singh](https://21st.dev/@hellocloud.webdev/components/ai-prompt-composer-with-model-selector) and [Chat with Model Menu by Ephraim Duncan](https://21st.dev/@ephraimduncan/components/chat-02) on 21st.dev. Their listings identify an MIT license. The references informed the grouped toolbar, model disclosure, and restrained transcript layout.

LocalMind's implementation is original and uses its existing React components, theme tokens and local-model settings. No registry component source or additional UI package was installed. The reference previews' commercial model lists and upgrade flows are not part of this app.

## Calm surfaces, glass and spotlight

Light mode uses a soft sage and off-white palette; dark mode uses slate with muted blue-green ambient gradients. Backgrounds are static CSS gradients, bundled locally without images, shaders or network requests. Reading surfaces and source menus remain legible and separate from decoration.

Action buttons have a shared glass finish: translucent fill, a thin reflective edge, inset highlights and a restrained shadow. Primary actions use the sage accent, destructive actions keep their error styling, and disabled controls remain visibly inactive. Unsupported backdrop blur falls back to solid surfaces. Reduced-transparency and forced-color preferences remove the glass treatment where appropriate.

The composer and starter cards have a pointer-following spotlight. It updates CSS at most once per animation frame, without React renders or document-wide listeners, and cancels pending work on exit/unmount. Touch and coarse pointers do not track. Reduced-motion preferences disable spotlight effects and button transitions; keyboard focus retains a visible outline.

Visual references: [Spotlight Card by Hossain Jahed](https://21st.dev/@jahed/components/spotlight-card) and [Liquid Glass Button by Ali Imam](https://21st.dev/@designali-in/components/liquid-glass-button). Their public previews were reviewed; their component licenses were not stated in the retrieved listings. These are original CSS and React implementations of the visual patterns, not copies of registry source.
