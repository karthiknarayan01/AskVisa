import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Sidebar } from "./Sidebar";

const now = new Date().toISOString();
const sessions = [
  { id: "a", title: "When does my grace period end?", created_at: now, updated_at: now },
  { id: "b", title: "Does Infosys sponsor green cards?", created_at: now, updated_at: now },
];

function renderSidebar(onDelete = vi.fn()) {
  render(<Sidebar sessions={sessions} current="a" open={false} onClose={() => {}} onOpen={() => {}} onDelete={onDelete} />);
  return onDelete;
}

describe("deleting a conversation", () => {
  afterEach(cleanup);

  it("asks first, then deletes on the second click", () => {
    const onDelete = renderSidebar();
    fireEvent.click(screen.getByRole("button", { name: 'Delete "Does Infosys sponsor green cards?"' }));
    expect(onDelete).not.toHaveBeenCalled();
    expect(screen.getByText("Delete for good?")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(onDelete).toHaveBeenCalledWith("b");
  });

  it("keeps it with Keep or Escape", () => {
    const onDelete = renderSidebar();
    fireEvent.click(screen.getByRole("button", { name: 'Delete "When does my grace period end?"' }));
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));
    expect(screen.queryByText("Delete for good?")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: 'Delete "When does my grace period end?"' }));
    fireEvent.keyDown(screen.getByRole("button", { name: "Keep" }), { key: "Escape" });
    expect(screen.queryByText("Delete for good?")).toBeNull();
    expect(onDelete).not.toHaveBeenCalled();
  });
});
