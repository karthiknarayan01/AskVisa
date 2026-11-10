// Small line icons, drawn at 20px on a 24px grid with the current text colour.

type Props = { size?: number };
const base = (size: number) => ({
  width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
  strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true,
});

export const SendIcon = ({ size = 20 }: Props) => (
  <svg {...base(size)}><path d="M12 19V5" /><path d="m5 12 7-7 7 7" /></svg>
);
export const StopIcon = ({ size = 20 }: Props) => (
  <svg {...base(size)}><rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor" stroke="none" /></svg>
);
export const PenIcon = ({ size = 20 }: Props) => (
  <svg {...base(size)}><path d="M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17v3Z" /><path d="m14.5 7.5 3 3" /></svg>
);
export const MenuIcon = ({ size = 20 }: Props) => (
  <svg {...base(size)}><path d="M4 7h16M4 12h16M4 17h10" /></svg>
);
export const CloseIcon = ({ size = 20 }: Props) => (
  <svg {...base(size)}><path d="m6 6 12 12M18 6 6 18" /></svg>
);
export const DotsIcon = ({ size = 18 }: Props) => (
  <svg {...base(size)}><circle cx="5" cy="12" r="1.3" fill="currentColor" stroke="none" /><circle cx="12" cy="12" r="1.3" fill="currentColor" stroke="none" /><circle cx="19" cy="12" r="1.3" fill="currentColor" stroke="none" /></svg>
);
export const PencilIcon = ({ size = 16 }: Props) => (
  <svg {...base(size)}><path d="M4 20h4L19 9l-4-4L4 16v4z" /><path d="M14 6l4 4" /></svg>
);
export const TrashIcon = ({ size = 16 }: Props) => (
  <svg {...base(size)}><path d="M4 7h16" /><path d="M10 11v6M14 11v6" /><path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12" /><path d="M9 7V4h6v3" /></svg>
);
export const ChevronIcon = ({ size = 16 }: Props) => (
  <svg {...base(size)}><path d="m9 6 6 6-6 6" /></svg>
);
