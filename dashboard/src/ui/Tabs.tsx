"use client";

import { useRef, type KeyboardEvent } from "react";
import css from "./Tabs.module.css";

export type TabItem<Id extends string> = { id: Id; label: string };

/** Id of the panel a tab controls; the consumer renders it with this id. */
export const tabPanelId = (prefix: string) => `${prefix}-panel`;
export const tabId = (prefix: string, id: string) => `${prefix}-tab-${id}`;

/**
 * WAI-ARIA tabs with automatic activation: one tab stop, arrows, Home and End
 * move and select. The consumer renders one panel with `tabPanelId(prefix)`.
 */
export function Tabs<Id extends string>({
  items,
  selected,
  onSelect,
  label,
  prefix,
}: {
  items: readonly TabItem<Id>[];
  selected: Id;
  onSelect: (id: Id) => void;
  label: string;
  prefix: string;
}) {
  const list = useRef<HTMLDivElement>(null);
  function move(event: KeyboardEvent, index: number) {
    const last = items.length - 1;
    const target =
      event.key === "ArrowRight"
        ? (index + 1) % items.length
        : event.key === "ArrowLeft"
          ? (index - 1 + items.length) % items.length
          : event.key === "Home"
            ? 0
            : event.key === "End"
              ? last
              : null;
    if (target === null) return;
    event.preventDefault();
    onSelect(items[target].id);
    list.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[target]?.focus();
  }
  return (
    <div aria-label={label} className={css.tabs} ref={list} role="tablist">
      {items.map((item, index) => {
        const active = item.id === selected;
        return (
          <button
            aria-controls={tabPanelId(prefix)}
            aria-selected={active}
            className={css.tab}
            id={tabId(prefix, item.id)}
            key={item.id}
            onClick={() => onSelect(item.id)}
            onKeyDown={(event) => move(event, index)}
            role="tab"
            tabIndex={active ? 0 : -1}
            type="button"
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}
