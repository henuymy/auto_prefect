import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";
import { Toaster } from "sonner";
import "./index.css";

const ConfigCenter = lazy(() => import("./App"));

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Suspense
      fallback={
        <div className="dashboard-shell flex min-h-dvh items-center justify-center text-sm font-bold text-slate-400">
          正在加载...
        </div>
      }
    >
      <ConfigCenter />
    </Suspense>
    <Toaster richColors position="top-right" duration={1600} closeButton />
  </React.StrictMode>,
);
