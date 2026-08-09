import type { Metadata } from "next";

import "./globals.css";


export const metadata: Metadata = {
  title: "见字 · 对话人格",
  description: "从共同聊天中，慢慢长出一个可以被理解和修正的对话人格。",
};


export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body suppressHydrationWarning>{children}</body>
    </html>
  );
}
