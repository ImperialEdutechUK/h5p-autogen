import "./globals.css";

export const metadata = {
  title: "H5P Activity Generator",
  description: "AI-assisted H5P activity generation",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
