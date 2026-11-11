/** Run a row's primary native link when a non-interactive part of the row is clicked. */
export function activateRowLink(
  target: EventTarget | null,
  currentTarget: HTMLElement,
  modifiers?: Pick<MouseEvent, "ctrlKey" | "metaKey" | "shiftKey" | "altKey">,
) {
  const element = target instanceof Element ? target : null;
  if (element?.closest("a,button,input,select,textarea,[role=button]")) return;

  const link = currentTarget.querySelector<HTMLAnchorElement>('a[data-row-primary][href]')
    ?? currentTarget.querySelector<HTMLAnchorElement>("a[href]");
  const MouseEventConstructor = currentTarget.ownerDocument.defaultView?.MouseEvent ?? MouseEvent;
  link?.dispatchEvent(new MouseEventConstructor("click", {
    bubbles: true,
    cancelable: true,
    ctrlKey: modifiers?.ctrlKey,
    metaKey: modifiers?.metaKey,
    shiftKey: modifiers?.shiftKey,
    altKey: modifiers?.altKey,
  }));
}
