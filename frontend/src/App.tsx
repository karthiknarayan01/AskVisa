// The app: the conversation list on the left, the open conversation and the message box on the right.

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Composer } from "./components/Composer";
import { EmptyState } from "./components/EmptyState";
import { MenuIcon } from "./components/icons";
import { Sidebar } from "./components/Sidebar";
import { SidebarResizer, useSidebarWidth } from "./components/SidebarResizer";
import { InkFilter } from "./components/Stamp";
import { TurnView } from "./components/TurnView";
import { useChat } from "./useChat";

export default function App() {
  const chat = useChat();
  const [menuOpen, setMenuOpen] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useSidebarWidth();
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  // Follow the answer as it streams, unless the reader has scrolled up to read something.
  const onScroll = () => {
    const el = scroller.current;
    if (el) pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };
  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    if (chat.turns.length === 0) el.scrollTop = 0;
    else if (pinned.current) el.scrollTop = el.scrollHeight;
  }, [chat.turns]);
  useEffect(() => {
    pinned.current = true;
    setMenuOpen(false);
  }, [chat.sessionId]);

  const send = (text: string) => {
    pinned.current = true;
    chat.send(text);
  };

  const title = chat.sessions.find((s) => s.id === chat.sessionId)?.title ?? "";
  useEffect(() => {
    document.title = title || "AskVisa";
  }, [title]);

  const modelNote = chat.model
    ? chat.model.provider === "ollama"
      ? `${chat.model.model}, running on this computer`
      : chat.model.model
    : "";

  return (
    <div className="app" style={{ "--sidebar-width": `${sidebarWidth}px` } as React.CSSProperties}>
      <InkFilter />
      <Sidebar
        sessions={chat.sessions}
        current={chat.sessionId}
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
        onOpen={(id) => chat.navigate(id)}
        onRename={chat.rename}
        onDelete={chat.remove}
      />
      <SidebarResizer width={sidebarWidth} onResize={setSidebarWidth} />
      {menuOpen && <div className="scrim" onClick={() => setMenuOpen(false)} />}
      <main className="main">
        <header className="topbar">
          <button type="button" className="icon-button topbar__menu" onClick={() => setMenuOpen(true)} aria-label="Show conversations">
            <MenuIcon />
          </button>
          {title && <h1 className="topbar__title">{title}</h1>}
          <span className="topbar__model">{modelNote}</span>
          {chat.status === "reconnecting" && (
            <span className="topbar__status" role="status">
              <span className="topbar__status-dot" aria-hidden="true" /> Reconnecting…
            </span>
          )}
        </header>
        <div className="scroller" ref={scroller} onScroll={onScroll}>
          <div className="column">
            {chat.turns.length === 0 && !chat.running ? (
              <EmptyState onPick={send} />
            ) : (
              chat.turns.map((turn, i) => (
                <TurnView
                  key={turn.key}
                  turn={turn}
                  live={chat.running && i === chat.turns.length - 1}
                  isLast={i === chat.turns.length - 1}
                  onRetry={chat.resume}
                />
              ))
            )}
            {chat.interrupted && !chat.running && (
              <div className="end-note" role="alert">
                <p>The last answer was cut off before it finished.</p>
                <button type="button" className="button button--quiet" onClick={chat.resume}>Finish it</button>
              </div>
            )}
          </div>
        </div>
        <div className="dock">
          {chat.error && <p className="dock__error" role="alert">{chat.error}</p>}
          <Composer running={chat.running} disabled={!chat.model} offline={chat.status !== "open"} onSend={send} onStop={chat.stop} />
          <p className="dock__note">General information, not legal advice. For decisions that matter, talk to an immigration attorney.</p>
        </div>
      </main>
    </div>
  );
}
