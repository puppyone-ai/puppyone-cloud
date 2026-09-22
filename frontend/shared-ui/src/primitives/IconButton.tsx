import type { ButtonHTMLAttributes, ReactNode } from "react";

export type IconButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> & {
  icon: ReactNode;
  label: string;
};

export function IconButton({
  icon,
  label,
  className,
  type = "button",
  ...props
}: IconButtonProps) {
  const classes = ["po-icon-button", className].filter(Boolean).join(" ");
  return (
    <button className={classes} type={type} aria-label={label} title={props.title ?? label} {...props}>
      {icon}
    </button>
  );
}

