import type { ButtonHTMLAttributes, ReactNode } from "react";

export type ButtonTone = "neutral" | "primary" | "danger";

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  tone?: ButtonTone;
  leadingIcon?: ReactNode;
};

export function Button({
  tone = "neutral",
  leadingIcon,
  children,
  className,
  type = "button",
  ...props
}: ButtonProps) {
  const classes = ["po-button", `po-button--${tone}`, className].filter(Boolean).join(" ");
  return (
    <button className={classes} type={type} {...props}>
      {leadingIcon && <span className="po-button__icon">{leadingIcon}</span>}
      <span className="po-button__label">{children}</span>
    </button>
  );
}

