import Link from "next/link";
import { Header } from "@/components/Header";
import { Footer } from "@/components/Footer";
import { LensMark } from "@/components/Wordmark";

export default function NotFound() {
  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <Header />
      <main id="main" className="notfound">
        <div className="wrap notfound__wrap">
          <LensMark className="notfound__lens" />
          <h1>This page is not here.</h1>
          <p className="head__sub">We looked through the lens twice. The link may be out of date.</p>
          <Link className="pill pill--dark pill--lg" href="/">
            Back to the start
          </Link>
        </div>
      </main>
      <Footer />
    </>
  );
}
