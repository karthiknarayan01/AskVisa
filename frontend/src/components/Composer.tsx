// The message box: Enter sends, Shift+Enter adds a line, Escape stops a running answer.
// While the connection is down (offline) you can keep typing; sending waits until it's back.

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { SendIcon, StopIcon } from "./icons";

type Props = { running: boolean; disabled: boolean; offline: boolean; onSend: (text: string) => void; onStop: () => void };

export function Composer({ running, disabled, offline, onSend, onStop }: Props) {
  const [text, setText] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 220)}px`;
  }, [text]);

  const send = () => {
    if (!text.trim() || running || disabled || offline) return;
    onSend(text);
    setText("");
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    } else if (e.key === "Escape" && running) {
      onStop();
    }
  };

  return (
    <form className="composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
      <label className="visually-hidden" htmlFor="message">Your question</label>
      <textarea
        id="message"
        ref={box}
        rows={1}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Ask about your visa, status or green card"
        disabled={disabled}
        autoFocus
      />
      {running ? (
        <button type="button" className="composer__button composer__button--stop" onClick={onStop} aria-label="Stop the answer">
          <StopIcon />
        </button>
      ) : (
        <button type="submit" className="composer__button" disabled={!text.trim() || disabled || offline} aria-label="Send">
          <SendIcon />
        </button>
      )}
    </form>
  );
}
