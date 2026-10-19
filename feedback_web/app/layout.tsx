import type {Metadata} from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI 情报雷达反馈",
  description: "AI 情报雷达匿名反馈与阅读统计",
  robots: {
    index: false,
    follow: false,
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
