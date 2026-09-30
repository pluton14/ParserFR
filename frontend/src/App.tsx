import { Navigate, NavLink, Route, Routes } from "react-router-dom";
import DictionariesPage from "./pages/DictionariesPage";
import CorpusPage from "./pages/CorpusPage";
import AnalysesPage from "./pages/AnalysesPage";
import AnalysisDetailPage from "./pages/AnalysisDetailPage";

// Находка 2026-09-29: демо-развёртывание (Cloudflare) показывает
// зафиксированный снимок базы без сбора — вкладка «Корпус» там не нужна
// и не должна быть даже доступна по прямой ссылке. Локально (npm run dev,
// без переменной) вкладка остаётся — это рабочий инструмент разработчика.
// Задаётся при СБОРКЕ (не в рантайме): VITE_HIDE_CORPUS_TAB=true npm run build.
const HIDE_CORPUS_TAB = import.meta.env.VITE_HIDE_CORPUS_TAB === "true";

export default function App() {
  return (
    <>
      <header className="app-header">
        <h1>ParserFR</h1>
        <nav className="app-nav">
          <NavLink to="/" end>Словари</NavLink>
          {!HIDE_CORPUS_TAB && <NavLink to="/corpus">Корпус</NavLink>}
          <NavLink to="/analyses">Анализ</NavLink>
        </nav>
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<DictionariesPage />} />
          <Route
            path="/corpus"
            element={HIDE_CORPUS_TAB ? <Navigate to="/" replace /> : <CorpusPage />}
          />
          <Route path="/analyses" element={<AnalysesPage />} />
          <Route path="/analyses/:id" element={<AnalysisDetailPage />} />
        </Routes>
      </main>
    </>
  );
}
