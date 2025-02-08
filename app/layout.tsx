import "./globals.css";
import { GeistSans } from "geist/font/sans";
import { Toaster } from "sonner";
import { cn } from "@/lib/utils";
import { Navbar } from "@/components/navbar";
import { SidebarProvider, SidebarInset } from "@/components/ui/sidebar";
import { AppSidebar } from "@/components/app-sidebar";
import { ThemeProvider } from "next-themes";

export const metadata = {
  title: "Emilio Esposito - Portfolio",
  description:
    "Emilio Esposito's personal portfolio and platform for production rental property management apps.",
  openGraph: {
    images: [
      {
        url: "/og?title=Emilio Esposito - Portfolio",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    images: [
      {
        url: "/og?title=Emilio Esposito - Portfolio",
      },
    ],
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head></head>
      <body className={cn(GeistSans.className, "antialiased")}>
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
          <Toaster position="top-center" richColors />
          <SidebarProvider>
            <AppSidebar />
            <SidebarInset>
              <Navbar />
              {children}
            </SidebarInset>
          </SidebarProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
