import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ChatStarters } from "../components/ChatStarters";

let scheduled: FrameRequestCallback | undefined;
let reducedMotion = false;
let finePointer = true;

beforeEach(() => {
  reducedMotion = false;
  finePointer = true;
  scheduled = undefined;
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: query.includes("reduced-motion") ? reducedMotion : finePointer }));
  vi.stubGlobal("requestAnimationFrame", vi.fn((callback: FrameRequestCallback) => { scheduled = callback; return 1; }));
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function move(element: HTMLElement, x: number, y: number, pointerType = "mouse") {
  const event = new Event("pointermove", { bubbles: true });
  Object.assign(event, { clientX: x, clientY: y, pointerType });
  fireEvent(element, event);
}

it("coalesces pointer updates and preserves the starter action", () => {
  const choose = vi.fn();
  render(<ChatStarters onChoose={choose} />);
  const button = screen.getByRole("button", { name: /Think it through/ });
  vi.spyOn(button, "getBoundingClientRect").mockReturnValue({ left: 10, top: 20 } as DOMRect);
  move(button, 30, 50);
  move(button, 60, 80);
  expect(requestAnimationFrame).toHaveBeenCalledTimes(1);
  scheduled?.(0);
  expect(button.style.getPropertyValue("--spotlight-x")).toBe("50px");
  expect(button.style.getPropertyValue("--spotlight-y")).toBe("60px");
  fireEvent.click(button);
  expect(choose).toHaveBeenCalledWith("Help me think through an idea: ");
  fireEvent.pointerLeave(button);
  expect(button.dataset.spotlightActive).toBeUndefined();
  expect(button.style.getPropertyValue("--spotlight-x")).toBe("");
});

it("does not track touch, coarse pointers or reduced-motion input", () => {
  render(<ChatStarters onChoose={vi.fn()} />);
  const button = screen.getByRole("button", { name: /Find the words/ });
  move(button, 30, 50, "touch");
  reducedMotion = true;
  move(button, 30, 50);
  reducedMotion = false;
  finePointer = false;
  move(button, 30, 50);
  expect(requestAnimationFrame).not.toHaveBeenCalled();
  expect(button.dataset.spotlightActive).toBeUndefined();
});

it("cancels pending effects on pointer cancellation and unmount", () => {
  const view = render(<ChatStarters onChoose={vi.fn()} />);
  const button = screen.getByRole("button", { name: /Make a plan/ });
  move(button, 30, 50);
  fireEvent.pointerCancel(button);
  expect(cancelAnimationFrame).toHaveBeenCalledTimes(1);
  move(button, 30, 50);
  view.unmount();
  expect(cancelAnimationFrame).toHaveBeenCalledTimes(2);
});
