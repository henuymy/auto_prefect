import React from "react";
import ReactDOM from "react-dom/client";
import "./index.css";
import "./dashboard/dashboard-cockpit.css";
import { DashboardCockpit } from "./dashboard/DashboardCockpit";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <DashboardCockpit />
  </React.StrictMode>,
);
