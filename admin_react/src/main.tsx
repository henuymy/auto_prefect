import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";
import { Toaster } from "sonner";
import "./index.css";

const ConfigCenter = lazy(() => import("./App"));
const DashboardPage = lazy(() =>
  import("./components/dashboard/DashboardPage").then((module) => ({
    default: module.DashboardPage,
  })),
);

function Root() {
  const [hash, setHash] = React.useState(window.location.hash);

  React.useEffect(() => {
    const handleHashChange = () => setHash(window.location.hash);
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);

  return (
    <Suspense
      fallback={
        <div className="dashboard-shell flex min-h-dvh items-center justify-center text-sm font-bold text-slate-400">
          正在加载...
        </div>
      }
    >
      {hash === "#dashboard" ? <DashboardPage /> : <ConfigCenter />}
    </Suspense>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Root />
    <Toaster richColors position="top-right" duration={1600} closeButton />
  </React.StrictMode>,
);
