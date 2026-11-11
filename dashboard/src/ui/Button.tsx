import type { ComponentPropsWithRef, ReactNode } from "react";
import css from "./Button.module.css";

export type ButtonProps = ComponentPropsWithRef<"button"> & {
  variant?: "primary" | "secondary" | "ghost";
  size?: "sm" | "md";
  icon?: ReactNode;
};

/** Native button with the Real Jaén variants; it knows nothing about the domain. */
export function Button({
  children,
  className = "",
  icon,
  size = "md",
  type = "button",
  variant = "secondary",
  ...props
}: ButtonProps) {
  return (
    <button
      {...props}
      className={`${css.button} ${css[variant]} ${css[size]} ${className}`.trim()}
      type={type}
    >
      {icon}
      <span>{children}</span>
    </button>
  );
}
