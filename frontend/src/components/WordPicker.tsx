import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { ContextWord } from "../api/types";

// Поле с выпадающим списком слов и множественным выбором: выбранные слова —
// чипы внутри поля. Можно кликать по списку, листать стрелками или набирать
// буквы (список сужается); слово, которого нет в списке, тоже можно добавить.
export default function WordPicker({
  label,
  values,
  onChange,
  options,
}: {
  label: string;
  values: string[];
  onChange: (values: string[]) => void;
  options: ContextWord[];
}) {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const inputId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const typed = text.trim().toLowerCase();
  const filtered = useMemo(() => {
    const free = options.filter((o) => !values.includes(o.word));
    const starts = free.filter((o) => o.word.toLowerCase().startsWith(typed));
    const contains = free.filter(
      (o) => !o.word.toLowerCase().startsWith(typed) && o.word.toLowerCase().includes(typed),
    );
    return [...starts, ...contains].slice(0, 100);
  }, [options, values, typed]);

  useEffect(() => setActive(0), [typed, open]);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  useEffect(() => {
    const el = listRef.current?.children[active] as HTMLElement | undefined;
    el?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const add = (word: string) => {
    const w = word.trim();
    if (w && !values.includes(w)) onChange([...values, w]);
    setText("");
  };
  const remove = (word: string) => onChange(values.filter((v) => v !== word));

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!open) setOpen(true);
      else setActive((i) => Math.min(i + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      if (open && filtered[active]) add(filtered[active].word);
      else add(text);
    } else if (e.key === "Backspace" && text === "" && values.length > 0) {
      remove(values[values.length - 1]);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div className="picker" ref={rootRef}>
      <label htmlFor={inputId}>{label}</label>
      <div className="picker-field" onClick={() => setOpen(true)}>
        {values.map((v) => (
          <span key={v} className="picker-chip">
            {v}
            <button
              type="button"
              aria-label={`Убрать ${v}`}
              onMouseDown={(e) => e.preventDefault()}
              onClick={(e) => {
                e.stopPropagation();
                remove(v);
              }}
            >
              ×
            </button>
          </span>
        ))}
        <input
          id={inputId}
          type="text"
          value={text}
          placeholder={values.length === 0 ? "любое" : ""}
          onChange={(e) => {
            setText(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={onKeyDown}
          autoComplete="off"
        />
        <button
          type="button"
          className="picker-caret"
          aria-label="Показать список"
          onClick={(e) => {
            e.stopPropagation();
            setOpen((o) => !o);
          }}
        >
          ▾
        </button>
      </div>
      {open && (
        <div className="picker-list" ref={listRef}>
          {filtered.length === 0 ? (
            <div className="picker-empty">
              {typed ? "Нет такого среди найденных — Enter добавит введённое слово" : "Все слова выбраны"}
            </div>
          ) : (
            filtered.map((o, i) => (
              <div
                key={o.word}
                className={`picker-item${i === active ? " picker-item-active" : ""}`}
                onMouseDown={(e) => {
                  e.preventDefault();
                  add(o.word);
                }}
                onMouseEnter={() => setActive(i)}
              >
                <span>{o.word}</span>
                <span className="muted">{o.count.toLocaleString("ru-RU")}</span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
