import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { DEFAULT_WIDTH, MIN_WIDTH, SidebarResizer, useSidebarWidth } from "./SidebarResizer";

function Harness() {
  const [width, setWidth] = useSidebarWidth();
  return (
    <>
      <output data-testid="width">{width}</output>
      <SidebarResizer width={width} onResize={setWidth} />
    </>
  );
}

// jsdom has no PointerEvent, so pointer events would arrive without clientX or button.
if (!("PointerEvent" in window)) {
  class PointerEvent extends MouseEvent {
    pointerId = 1;
  }
  Object.assign(window, { PointerEvent });
}

const width = () => Number(screen.getByTestId("width").textContent);
const handle = () => screen.getByRole("separator", { name: "Resize the conversation list" });

describe("resizing the conversation list", () => {
  beforeEach(() => {
    localStorage.clear();
    window.innerWidth = 1400;
  });
  afterEach(cleanup);

  it("follows the pointer while dragging, and only then", () => {
    render(<Harness />);
    expect(width()).toBe(DEFAULT_WIDTH);
    fireEvent.pointerMove(handle(), { clientX: 400 });
    expect(width()).toBe(DEFAULT_WIDTH);
    fireEvent.pointerDown(handle(), { clientX: DEFAULT_WIDTH, button: 0 });
    fireEvent.pointerMove(handle(), { clientX: 360 });
    expect(width()).toBe(360);
    fireEvent.pointerUp(handle(), { clientX: 360 });
    fireEvent.pointerMove(handle(), { clientX: 420 });
    expect(width()).toBe(360);
  });

  it("stays between the minimum and the maximum", () => {
    render(<Harness />);
    fireEvent.pointerDown(handle(), { clientX: DEFAULT_WIDTH, button: 0 });
    fireEvent.pointerMove(handle(), { clientX: 20 });
    expect(width()).toBe(MIN_WIDTH);
    fireEvent.pointerMove(handle(), { clientX: 2000 });
    expect(width()).toBe(Number(handle().getAttribute("aria-valuemax")));
  });

  it("moves with the arrow keys, and double-click resets it", () => {
    render(<Harness />);
    fireEvent.keyDown(handle(), { key: "ArrowRight" });
    expect(width()).toBe(DEFAULT_WIDTH + 16);
    fireEvent.keyDown(handle(), { key: "Home" });
    expect(width()).toBe(MIN_WIDTH);
    fireEvent.doubleClick(handle());
    expect(width()).toBe(DEFAULT_WIDTH);
  });

  it("remembers the width for next time", () => {
    const first = render(<Harness />);
    fireEvent.keyDown(handle(), { key: "ArrowLeft" });
    first.unmount();
    render(<Harness />);
    expect(width()).toBe(DEFAULT_WIDTH - 16);
  });
});
