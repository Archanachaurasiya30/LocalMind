import { useEffect, useRef, type PointerEvent } from "react";

/** Decorative pointer lighting; updates CSS once per frame without rendering React. */
export function useSpotlight<T extends HTMLElement>() {
  const frame = useRef<number | null>(null);
  const pending = useRef<{ element: T; x: number; y: number } | null>(null);

  const clear = (element: T) => {
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = null;
    pending.current = null;
    delete element.dataset.spotlightActive;
    element.style.removeProperty("--spotlight-x");
    element.style.removeProperty("--spotlight-y");
  };

  useEffect(() => () => {
    if (frame.current !== null) cancelAnimationFrame(frame.current);
  }, []);

  return {
    onPointerMove: (event: PointerEvent<T>) => {
      if (event.pointerType !== "mouse" ||
          !window.matchMedia("(hover: hover) and (pointer: fine)").matches ||
          window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
      pending.current = { element: event.currentTarget, x: event.clientX, y: event.clientY };
      if (frame.current !== null) return;
      frame.current = requestAnimationFrame(() => {
        frame.current = null;
        const position = pending.current;
        if (!position?.element.isConnected) return;
        const { element, x, y } = position;
        const rect = element.getBoundingClientRect();
        element.style.setProperty("--spotlight-x", `${x - rect.left}px`);
        element.style.setProperty("--spotlight-y", `${y - rect.top}px`);
        element.dataset.spotlightActive = "true";
      });
    },
    onPointerLeave: (event: PointerEvent<T>) => clear(event.currentTarget),
    onPointerCancel: (event: PointerEvent<T>) => clear(event.currentTarget),
  };
}
