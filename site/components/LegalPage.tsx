import type { ReactNode } from "react";
import { Header } from "./Header";
import { Footer } from "./Footer";

export function LegalPage({ title, intro, children }: { title: string; intro?: ReactNode; children: ReactNode }) {
  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <Header />
      <main id="main" className="legal">
        <div className="wrap legal__wrap">
          <h1>{title}</h1>
          {intro ? <div className="legal__intro">{intro}</div> : null}
          <div className="legal__body glass">{children}</div>
        </div>
      </main>
      <Footer />
    </>
  );
}
