import { useEffect, useState } from "react";
import {
  Link,
  NavLink,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpRight,
  ArrowRight,
  Blocks,
  BriefcaseBusiness,
  CircleUserRound,
  FileText,
  Fingerprint,
  GitBranch,
  GraduationCap,
  LayoutDashboard,
  LogOut,
  Menu,
  MessageSquare,
  Moon,
  Search,
  Settings2,
  Sun,
  Monitor,
  X,
} from "lucide-react";
import { Session, useSession } from "./session";
import { Heading, Panel, Loading, Action, Go } from "./ui";
import {
  Auth,
  Profile,
  Privacy,
  Documents,
  Fsp,
  Assessment,
  TestPage,
  Dashboard,
  Docs,
} from "./candidate";
import {
  Company,
  Needs,
  NeedEditor,
  RunPage,
  Candidates,
  CandidatePage,
  Invitations,
  Conversations,
  Conversation,
  Vacancies,
  VacancyEditor,
  VacancyPage,
  Applications,
  Assignments,
  AssignmentPage,
} from "./hiring";
import logoLight from "../../brand/assets/logo/logo-short-on-light.svg";
import logoDark from "../../brand/assets/logo/logo-short-on-dark.svg";

export function Brand() {
  return (
    <Link to="/" className="wordmark" aria-label="Сцепка, главная">
      <span className="brand-symbol">
        <GitBranch size={25} strokeWidth={2.5} />
      </span>
      <span>
        сцепка<span className="wordmark-dot">.</span>
      </span>
    </Link>
  );
}
export function Federation() {
  return (
    <span className="federation">
      <img
        className="fsp-only-light"
        src={logoLight}
        alt="Федерация спортивного программирования"
      />
      <img
        className="fsp-only-dark"
        src={logoDark}
        alt="Федерация спортивного программирования"
      />
    </span>
  );
}
export function Theme() {
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem("scepka-theme") || "system";
    } catch {
      return "system";
    }
  });
  useEffect(() => {
    if (theme === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("scepka-theme", theme);
    } catch {}
  }, [theme]);
  return (
    <div className="theme-switch" role="group" aria-label="Тема интерфейса">
      {[
        ["light", Sun, "Светлая"],
        ["dark", Moon, "Тёмная"],
        ["system", Monitor, "Системная"],
      ].map(([value, Icon, label]) => {
        const I = Icon as typeof Sun;
        return (
          <button
            key={String(value)}
            aria-label={String(label)}
            aria-pressed={theme === value}
            onClick={() => setTheme(String(value))}
          >
            <I size={16} />
          </button>
        );
      })}
    </div>
  );
}
function Landing() {
  const { user } = useSession();
  return (
    <div className="landing">
      <nav className="public-nav">
        <Brand />
        <div className="nav-links">
          <Link to="/docs">Как это работает</Link>
          <Link to="/vacancies">Вакансии</Link>
          <Theme />
          <Link
            className="button secondary"
            to={
              user
                ? user.roles.includes("candidate")
                  ? "/candidate"
                  : "/employer"
                : "/login"
            }
          >
            {user ? "В кабинет" : "Войти"}
            <ArrowUpRight size={16} />
          </Link>
        </div>
      </nav>
      <section className="hero">
        <div className="hero-copy">
          <p className="eyebrow">
            <span className="pulse-dot" /> Навыки становятся возможностями
          </p>
          <h1>
            Не просто резюме
            <br />
            <span>Точка сцепления</span>
          </h1>
          <p className="hero-description">
            Подтвердите то, что умеете. Найдите тех, кто нужен команде.
            Встретьтесь на платформе, где предложение начинается с конкретики
          </p>
          <div className="hero-buttons">
            <Link to="/register?role=candidate" className="button">
              Я ищу возможности
              <ArrowRight size={18} />
            </Link>
            <Link to="/register?role=employer" className="button secondary">
              Я собираю команду
              <ArrowUpRight size={18} />
            </Link>
          </div>
          <div className="hero-foot">
            <Federation />
            <span>Профиль сильнее слов</span>
          </div>
        </div>
        <div className="hero-art" aria-hidden="true">
          <div className="orbit orbit-one" />
          <div className="orbit orbit-two" />
          <div className="orbit orbit-three" />
          <span className="art-node n1" />
          <span className="art-node n2" />
          <span className="art-node n3" />
          <span className="art-node n4" />
          <span className="art-node n5" />
          <div className="art-center">
            <GitBranch size={72} />
          </div>
          <div className="floating-label fl1">
            [ навыки ]<span>Подтверждено тестом</span>
          </div>
          <div className="floating-label fl2">
            [ команда ]<span>Найдена связь</span>
          </div>
          <div className="art-coordinate">
            55.7558 / 37.6173
            <br />
            СОЕДИНЕНИЕ УСТАНОВЛЕНО
          </div>
        </div>
      </section>
      <div className="ticker">
        <span>Меньше случайных откликов</span>
        <span>Больше подтверждённых навыков</span>
        <span>Зарплата до начала общения</span>
      </div>
      <section className="landing-section">
        <Heading
          eyebrow="Механика связи"
          title="Работодатель делает первый шаг"
        >
          Вы не теряетесь среди сотен откликов. Ваш подтверждённый профиль
          помогает команде найти именно вас
        </Heading>
        <div className="three-grid">
          {[
            [
              "01",
              "Покажите, что умеете",
              "Профиль, входной опрос и индивидуальный тест. Заявленные и подтверждённые навыки всегда различаются",
              GraduationCap,
            ],
            [
              "02",
              "Найдите совпадение",
              "Опишите потребность команды. Исследуйте подборку, факторы соответствия и карту кандидатов",
              Search,
            ],
            [
              "03",
              "Начните с предложения",
              "Работодатель указывает зарплату и условия. Кандидат решает, отвечать ли и раскрывать ли контакты",
              MessageSquare,
            ],
          ].map(([n, title, desc, Icon]) => {
            const I = Icon as typeof Search;
            return (
              <Panel key={String(n)}>
                <div className="step-top">
                  <span className="step-number">{String(n)}</span>
                  <I size={25} />
                </div>
                <h3>{String(title)}</h3>
                <p>{String(desc)}</p>
              </Panel>
            );
          })}
        </div>
      </section>
      <section className="landing-cta">
        <p className="eyebrow">Следующий шаг — ваш</p>
        <h2>
          Хорошие команды
          <br />
          начинаются со сцепки
        </h2>
        <Link to="/register" className="button">
          Создать профиль
          <ArrowRight size={18} />
        </Link>
      </section>
      <footer className="public-footer">
        <Brand />
        <span>Сцепка · Проект хакатона ФСП</span>
        <Link to="/docs">
          Документация
          <ArrowUpRight size={15} />
        </Link>
      </footer>
    </div>
  );
}
const candidateNav = [
  ["", "Обзор", LayoutDashboard],
  ["/profile", "Мой профиль", CircleUserRound],
  ["/assessment", "Подтверждение навыков", GraduationCap],
  ["/documents", "Резюме и PDF", FileText],
  ["/invitations", "Предложения", BriefcaseBusiness],
  ["/conversations", "Сообщения", MessageSquare],
  ["/applications", "Мои отклики", GitBranch],
  ["/assignments", "Короткие задания", Blocks],
  ["/fsp", "Достижения ФСП", Fingerprint],
  ["/privacy", "Приватность", Settings2],
] as const;
const employerNav = [
  ["", "Обзор", LayoutDashboard],
  ["/needs", "Потребности команды", Search],
  ["/candidates", "Банк кандидатов", CircleUserRound],
  ["/invitations", "Предложения", BriefcaseBusiness],
  ["/conversations", "Сообщения", MessageSquare],
  ["/vacancies", "Вакансии", FileText],
  ["/applications", "Отклики", GitBranch],
  ["/assignments", "Короткие задания", Blocks],
  ["/company", "Компания", Blocks],
] as const;
function Cabinet({ role }: { role: "candidate" | "employer" }) {
  const { user, loading, logout } = useSession();
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const qc = useQueryClient();
  useEffect(() => {
    setOpen(false);
  }, [location.pathname]);
  if (loading) return <Loading />;
  if (!user) return <Navigate to="/login" replace />;
  if (!user.roles.includes(role))
    return (
      <main className="auth-layout">
        <Heading title="Этот кабинет недоступен">
          У аккаунта нет этой роли
        </Heading>
        <Go to={`/${user.roles[0]}`}>В свой кабинет</Go>
      </main>
    );
  return (
    <div className="workspace">
      <aside className={`sidebar ${open ? "open" : ""}`}>
        <div className="sidebar-brand">
          <Brand />
          <button
            className="mobile-only icon-button"
            aria-label="Закрыть меню"
            onClick={() => setOpen(false)}
          >
            <X size={20} />
          </button>
        </div>
        <p className="sidebar-caption">
          {role === "candidate" ? "Кабинет кандидата" : "Рабочее пространство"}
        </p>
        <nav aria-label="Навигация кабинета">
          {(role === "candidate" ? candidateNav : employerNav).map(
            ([path, label, Icon]) => (
              <NavLink key={path} end={path === ""} to={`/${role}${path}`}>
                <Icon size={19} />
                {label}
              </NavLink>
            ),
          )}
        </nav>
        <div className="sidebar-bottom">
          <Link to="/docs">
            <FileText size={18} />
            Документация
          </Link>
          <Federation />
        </div>
      </aside>
      {open && (
        <button
          className="menu-overlay"
          aria-label="Закрыть навигацию"
          onClick={() => setOpen(false)}
        />
      )}
      <div className="workspace-body">
        <header className="topbar">
          <button
            className="mobile-only icon-button"
            aria-label="Открыть меню"
            onClick={() => setOpen(true)}
          >
            <Menu size={22} />
          </button>
          <div className="breadcrumb">
            Сцепка <span>/</span>{" "}
            {role === "candidate" ? "Ваши возможности" : "Ваша команда"}
          </div>
          <div className="topbar-actions">
            {user.roles.length > 1 && (
              <Link
                onClick={() => qc.clear()}
                to={role === "candidate" ? "/employer" : "/candidate"}
                className="cabinet-switch"
              >
                {role === "candidate" ? "Работодателю" : "Кандидату"}
                <ArrowUpRight size={15} />
              </Link>
            )}
            <Theme />
            <span className="avatar" title={user.email}>
              {user.email.slice(0, 1).toUpperCase()}
            </span>
            <Action secondary run={logout}>
              <LogOut size={17} />
              <span className="desktop-only">Выйти</span>
            </Action>
          </div>
        </header>
        <main className="workspace-main" id="main">
          {!user.email_verified && (
            <div className="notice">
              {user.demo_auth ? "Демонстрационный режим · " : ""}Подтвердите
              почту, чтобы пользоваться всеми возможностями{" "}
              <Link to="/verify-email">Подтвердить →</Link>
            </div>
          )}
          <Outlet />
        </main>
        <footer className="workspace-footer">
          <span>сцепка. / подтверждённые связи</span>
          <Link to="/vacancies">Открытые вакансии ↗</Link>
        </footer>
      </div>
    </div>
  );
}
export function App() {
  return (
    <Session>
      <a className="skip-link" href="#main">
        К содержимому
      </a>
      <Routes>
        <Route path="/" element={<Landing />} />
        {[
          "login",
          "register",
          "forgot-password",
          "reset-password",
          "verify-email",
        ].map((path) => (
          <Route key={path} path={`/${path}`} element={<Auth mode={path} />} />
        ))}
        <Route path="/docs/*" element={<Docs />} />
        <Route path="/vacancies" element={<Vacancies publicView />} />
        <Route path="/vacancies/:id" element={<VacancyPage />} />
        <Route path="/candidate" element={<Cabinet role="candidate" />}>
          <Route index element={<Dashboard role="candidate" />} />
          <Route path="profile" element={<Profile />} />
          <Route path="privacy" element={<Privacy />} />
          <Route path="documents" element={<Documents />} />
          <Route path="fsp" element={<Fsp />} />
          <Route path="assessment" element={<Assessment />} />
          <Route path="tests/:id" element={<TestPage />} />
          {shared("candidate")}
        </Route>
        <Route path="/employer" element={<Cabinet role="employer" />}>
          <Route index element={<Dashboard role="employer" />} />
          <Route path="company" element={<Company />} />
          <Route path="needs" element={<Needs />} />
          <Route path="needs/new" element={<NeedEditor />} />
          <Route path="needs/:id" element={<NeedEditor />} />
          <Route path="runs/:id" element={<RunPage />} />
          <Route path="candidates" element={<Candidates />} />
          <Route path="candidates/:id" element={<CandidatePage />} />
          <Route path="vacancies" element={<Vacancies />} />
          <Route path="vacancies/new" element={<VacancyEditor />} />
          <Route path="vacancies/:id" element={<VacancyEditor />} />
          {shared("employer")}
        </Route>
        <Route
          path="*"
          element={
            <main className="auth-layout">
              <Heading title="Страница не найдена">
                Возможно, ссылка изменилась
              </Heading>
              <Go to="/">На главную</Go>
            </main>
          }
        />
      </Routes>
    </Session>
  );
}
function shared(role: "candidate" | "employer") {
  return (
    <>
      <Route path="invitations" element={<Invitations role={role} />} />
      <Route path="conversations" element={<Conversations role={role} />} />
      <Route path="conversations/:id" element={<Conversation role={role} />} />
      <Route path="applications" element={<Applications role={role} />} />
      <Route path="assignments" element={<Assignments role={role} />} />
      <Route path="assignments/:id" element={<AssignmentPage role={role} />} />
    </>
  );
}
