import type { ComponentPropsWithRef } from "react";
import css from "./Select.module.css";

/** Native select: platform keyboard behaviour; labels stay with the form. */
export function Select({ className = "", ...props }: ComponentPropsWithRef<"select">) {
  return <select {...props} className={`${css.select} ${className}`.trim()} />;
}
