import type { SVGProps } from 'react'

/** A small, consistent stroke-icon set (1.6px stroke, 20x20 viewBox) — used instead of
 * unicode glyphs so every icon in the app shares one visual language. */
function Icon({ children, ...props }: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      {children}
    </svg>
  )
}

export function IconSettings(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <circle cx="10" cy="10" r="2.6" />
      <path d="M10 3.4v1.8M10 14.8v1.8M16.6 10h-1.8M5.2 10H3.4M14.6 5.4l-1.3 1.3M6.7 13.3l-1.3 1.3M14.6 14.6l-1.3-1.3M6.7 6.7 5.4 5.4" />
    </Icon>
  )
}

export function IconTrash(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M4 5.5h12M8 5.5V4a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1v1.5M6 5.5l.7 10a1 1 0 0 0 1 .9h4.6a1 1 0 0 0 1-.9l.7-10" />
      <path d="M8.3 8.7v4.3M11.7 8.7v4.3" />
    </Icon>
  )
}

export function IconUpload(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M10 12.5V3.8M6.5 7.2 10 3.6l3.5 3.6" />
      <path d="M4 13v1.7A1.3 1.3 0 0 0 5.3 16h9.4a1.3 1.3 0 0 0 1.3-1.3V13" />
    </Icon>
  )
}

export function IconSend(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M17 3 2.5 9.1a.5.5 0 0 0 0 .9L8 12l2 5.5a.5.5 0 0 0 .9 0z" />
      <path d="M17 3 8 12" />
    </Icon>
  )
}

export function IconClose(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M5 5l10 10M15 5 5 15" />
    </Icon>
  )
}

export function IconChevronLeft(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M12.5 4.5 7 10l5.5 5.5" />
    </Icon>
  )
}

export function IconChevronRight(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M7.5 4.5 13 10l-5.5 5.5" />
    </Icon>
  )
}

export function IconFilePdf(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M5.5 2.5h6l3 3v11a1 1 0 0 1-1 1h-8a1 1 0 0 1-1-1v-13a1 1 0 0 1 1-1Z" />
      <path d="M11.5 2.5v3h3" />
    </Icon>
  )
}

export function IconFileDoc(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M5.5 2.5h6l3 3v11a1 1 0 0 1-1 1h-8a1 1 0 0 1-1-1v-13a1 1 0 0 1 1-1Z" />
      <path d="M11.5 2.5v3h3M7 11h6M7 13.5h6" />
    </Icon>
  )
}

export function IconFileText(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M5.5 2.5h6l3 3v11a1 1 0 0 1-1 1h-8a1 1 0 0 1-1-1v-13a1 1 0 0 1 1-1Z" />
      <path d="M7 9.5h6M7 12h6" />
    </Icon>
  )
}

export function IconSparkle(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M10 2.5c.5 2.6 1.4 4.4 3.9 5-2.5.6-3.4 2.4-3.9 5-.5-2.6-1.4-4.4-3.9-5 2.5-.6 3.4-2.4 3.9-5Z" />
      <path d="M15.5 12.8c.25 1 .7 1.7 1.7 2-1 .3-1.45.9-1.7 2-.25-1.1-.7-1.7-1.7-2 1-.3 1.45-1 1.7-2Z" />
    </Icon>
  )
}

export function IconInbox(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M3 10.5 5.4 4h9.2l2.4 6.5" />
      <path d="M3 10.5v4.2A1.3 1.3 0 0 0 4.3 16h11.4a1.3 1.3 0 0 0 1.3-1.3v-4.2h-4l-.8 1.8h-5.4L6 10.5Z" />
    </Icon>
  )
}

export function IconAlert(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M10 3.5 17 15.5H3Z" />
      <path d="M10 8.3v3M10 13.4v.1" />
    </Icon>
  )
}

export function IconCheck(props: SVGProps<SVGSVGElement>) {
  return (
    <Icon {...props}>
      <path d="M4 10.5 8 14.5 16 5.5" />
    </Icon>
  )
}
