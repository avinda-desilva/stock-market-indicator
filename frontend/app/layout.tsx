import type { Metadata } from "next";
import { Playfair_Display, DM_Sans } from "next/font/google";
import "./globals.css";
import { Toaster } from "react-hot-toast";
import { Navbar } from "@/components/dashboard/Navbar";

const playfair = Playfair_Display({
  subsets: ["latin"],
  variable: "--font-playfair",
  display: "swap",
  weight: ["400", "500", "600", "700"],
});

const dmSans = DM_Sans({
  subsets: ["latin"],
  variable: "--font-dm-sans",
  display: "swap",
  weight: ["300", "400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: "Stock Market Indicator — Trending Tickers",
  description: "Sector-based trending tickers and financial search engine",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${playfair.variable} ${dmSans.variable}`}>
      <body className="min-h-screen bg-[#F8F9FA] text-[#1A1A1A] antialiased">
        <Navbar />
        <main>{children}</main>
        <Toaster
          position="bottom-right"
          toastOptions={{
            style: {
              background: "#FFFFFF",
              color: "#1A1A1A",
              border: "1px solid #E5E7EB",
              boxShadow: "0 1px 4px rgba(0,0,0,0.08)",
            },
            success: { iconTheme: { primary: "#16A34A", secondary: "#F8F9FA" } },
            error: { iconTheme: { primary: "#B91C1C", secondary: "#F8F9FA" } },
          }}
        />
      </body>
    </html>
  );
}
