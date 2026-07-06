// Shared navigation/focus helpers for the Brain surface components.
export function navigateHash(route: string) {
  window.location.hash = route;
}

export function focusComposer() {
  document.querySelector<HTMLTextAreaElement>(".brain-composer textarea")?.focus();
}
