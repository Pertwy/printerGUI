import "./App.css";
import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import PrintPage from "./pages/PrintPage";
import UploadPage from "./pages/UploadPage";

export default function App() {
  return (
    <BrowserRouter>
      <nav className="app-nav" aria-label="Main">
        <NavLink
          to="/"
          end
          className={({ isActive }) =>
            "app-nav-link" + (isActive ? " app-nav-link--active" : "")
          }
        >
          Print
        </NavLink>
        <NavLink
          to="/upload"
          className={({ isActive }) =>
            "app-nav-link" + (isActive ? " app-nav-link--active" : "")
          }
        >
          Upload
        </NavLink>
      </nav>
      <Routes>
        <Route path="/" element={<PrintPage />} />
        <Route path="/upload" element={<UploadPage />} />
      </Routes>
    </BrowserRouter>
  );
}
