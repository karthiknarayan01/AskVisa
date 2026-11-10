import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Sidebar } from "./Sidebar";

const now = new Date().toISOString();
const sessions = [
  { id: "a", title: "H-1B layoff grace period", created_at: now, updated_at: now },
  { id: "b", title: "Infosys green card sponsorship", created_at: now, updated_at: now },
];

function renderSidebar() {
  const onRename = vi.fn();
  const onDelete = vi.fn();
  render(
    <Sidebar sessions={sessions} current="a" open={false} onClose={() => {}} onOpen={() => {}} onRename={onRename} onDelete={onDelete} />,
  );
  return { onRename, onDelete };
}

const openMenu = (title: string) => fireEvent.click(screen.getByRole("button", { name: `Options for "${title}"` }));

describe("a conversation's ⋯ menu", () => {
  afterEach(cleanup);

  it("offers Rename and Delete, and closes with Escape", () => {
    renderSidebar();
    openMenu("Infosys green card sponsorship");
    expect(screen.getAllByRole("menuitem").map((i) => i.textContent?.trim())).toEqual(["Rename", "Delete"]);
    expect(document.activeElement?.textContent?.trim()).toBe("Rename");
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("closes on a click elsewhere", () => {
    renderSidebar();
    openMenu("Infosys green card sponsorship");
    act(() => {
      document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    });
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("renames in place: Enter saves the new title, tidied", () => {
    const { onRename } = renderSidebar();
    openMenu("H-1B layoff grace period");
    fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
    const box = screen.getByRole("textbox", { name: "Conversation title" }) as HTMLInputElement;
    expect(box.value).toBe("H-1B layoff grace period");
    fireEvent.change(box, { target: { value: "  My   layoff  " } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onRename).toHaveBeenCalledWith("a", "My layoff");
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("keeps the old title on Escape, or when the new one is empty or the same", () => {
    const { onRename } = renderSidebar();
    for (const [value, key] of [["Something else", "Escape"], ["   ", "Enter"], ["H-1B layoff grace period", "Enter"]]) {
      openMenu("H-1B layoff grace period");
      fireEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
      const box = screen.getByRole("textbox", { name: "Conversation title" });
      fireEvent.change(box, { target: { value } });
      fireEvent.keyDown(box, { key });
    }
    expect(onRename).not.toHaveBeenCalled();
  });

  it("asks before deleting, and Keep cancels", () => {
    const { onDelete } = renderSidebar();
    openMenu("Infosys green card sponsorship");
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    expect(screen.getByText("Delete for good?")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));
    expect(onDelete).not.toHaveBeenCalled();

    openMenu("Infosys green card sponsorship");
    fireEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(onDelete).toHaveBeenCalledWith("b");
  });
});
