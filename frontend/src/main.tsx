import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "../..//brand/dist/tokens.css";
import "../..//brand/dist/base.css";
import "./style.css";
import { App } from "./App";
import Docs from "./Docs";

export const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, staleTime: 10000 } },
});
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename={import.meta.env.BASE_URL}>
        {import.meta.env.MODE === "docs" ? <Docs standalone /> : <App />}
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
