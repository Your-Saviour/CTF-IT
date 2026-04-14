import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Corp Employee Portal',
  description: 'Internal employee portal',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  )
}
