# Enterprise Agent Coding Standards & Architecture Guidelines

This document is the single source of truth for coding standards, architectural guardrails, design patterns, and quality engineering across enterprise workspaces:
- **Backend Services & APIs**: NestJS, Node.js, TypeScript (REST, GraphQL, microservices)
- **Cross-Platform Mobile Applications**: React Native, Expo (SDK 50+), Expo Router
- **Web Applications & Portals**: Next.js (App Router), React 19, Tailwind CSS
- **Product & Marketing Landing Pages**: High-conversion Jamstack / Next.js marketing sites
- **Enterprise Monorepos & Shared Packages**: Nx, Turborepo, pnpm workspaces

Every autonomous agent and software engineer must strictly adhere to these standards.

---

## Table of Contents
1. [Core Principles & Universal Software Architecture](#1-core-principles--universal-software-architecture)
2. [Sleek UI Design System & Precision Engineering (Web & Mobile)](#2-sleek-ui-design-system--precision-engineering-web--mobile)
3. [Product & Marketing Landing Pages (Next.js App Router)](#3-product--marketing-landing-pages-nextjs-app-router)
4. [Cross-Platform Mobile Standards (React Native / Expo)](#4-cross-platform-mobile-standards-react-native--expo)
5. [Web Application & Dashboard Standards (Next.js App Router)](#5-web-application--dashboard-standards-nextjs-app-router)
6. [Enterprise Backend API Standards (NestJS & Node.js)](#6-enterprise-backend-api-standards-nestjs--nodejs)
7. [Enterprise Monorepo, Testing & CI/CD Standards](#7-enterprise-monorepo-testing--cicd-standards)
8. [Universal Code Quality Checklist](#8-universal-code-quality-checklist)

---

## 1. Core Principles & Universal Software Architecture

### 1.1 Clean Architecture, SOLID & DRY
- **Clean Architecture & Separation of Concerns**: Enforce strict layer boundaries. Domain logic must never depend on database drivers, HTTP controllers, or UI frameworks.
- **SOLID Principles**:
  - *Single Responsibility*: Every class, service, and component must have one well-defined reason to change.
  - *Open/Closed*: Extend functionality by writing new strategies or plugins rather than modifying battle-tested core implementations.
  - *Liskov Substitution*: Subtypes must remain completely substitutable for their base types without altering expected behavior.
  - *Interface Segregation*: Prefer multiple fine-grained, role-specific interfaces over bloated universal interfaces.
  - *Dependency Inversion*: Depend on abstractions (interfaces/contracts), never concrete implementations.
- **DRY (Don't Repeat Yourself)**:
  - If business logic, navigation structures, styling utilities, or database queries are duplicated in two or more locations, extract them into a shared helper, component, or base class.
  - Parameterize differences instead of copy-pasting code blocks with minor adjustments.
  - Compute role- or tenant-specific variations dynamically from a canonical source rather than maintaining parallel duplicate arrays.

### 1.2 Composition Over Inheritance & Deep Trees
- Favor composition (passing components as props/children, injecting specialized services) over branching deep inheritance hierarchies.
- Reserve inheritance strictly for fundamental architectural contracts (e.g., `BaseEntity`, `BaseCrudService<T>`), while leveraging composition for layouts, wrappers, and feature extensions.

### 1.3 Concise, Idiomatic Code & Early Returns
- Keep functions and components compact and single-purpose (target < 150 lines per module/component).
- Always use early returns and guard clauses to eliminate nested conditional pyramids (`if (...) return;`).
- Prefer functional paradigms (mapping, filtering, reducing) over manual mutating loops.
- Avoid unnecessary temporary variables and deeply nested ternary expressions.

### 1.4 Strict Comment Discipline & Zero Dead Code
- **NEVER** write verbose, multi-paragraph comments or explain syntax that is already self-evident.
- Write **at most one short line** strictly to explain the non-obvious *why* (e.g., an external API workaround, complex mathematical formula, or unique business requirement).
- **Zero dead code policy**: Never leave commented-out code, unused imports, or ad-hoc debug statements (`console.log`) in the codebase. Delete them immediately — git history preserves past implementations.

### 1.5 Security, OWASP & 12-Factor Configuration
- **Zero hardcoded secrets**: Never commit API keys, JWT secrets, database connection strings, or private endpoints.
- **12-Factor Configuration**: Always inject configuration via environment variables through the framework's typed config system (`ConfigService`, `expo-constants`, or `process.env`).
- **OWASP Top 10 Compliance**:
  - Always validate and sanitize untrusted external input (DTOs, path parameters, headers, file uploads, and LLM outputs).
  - Use parameterized queries or ORM abstractions to prevent SQL/NoSQL injection.
  - Enforce secure HTTP headers (Helmet), strict CORS policies, and rate-limiting on sensitive endpoints.
  - Store passwords and sensitive tokens using strong hashing (`bcrypt`/`argon2` with appropriate salt rounds).

### 1.6 Structured Observability & Trace Context
- Use structured JSON logging (via Pino, Winston, or framework loggers) with log levels (`debug`, `info`, `warn`, `error`).
- Propagate a Correlation ID (`X-Correlation-Id`) across HTTP requests, background jobs, and microservice events to enable end-to-end distributed tracing.
- Never log personally identifiable information (PII), credentials, authorization tokens, or raw payment data.

---

## 2. Sleek UI Design System & Precision Engineering (Web & Mobile)
*Inspired by the Sleek UI Automotive Engineering & Industrial Design Systems (`sleek-ui/designs/bmw`)*

Front-end applications across web, dashboard, and mobile must deliver an uncompromising aesthetic of precision, performance, and industrial confidence. Avoid generic "vibe-coded" looks or unstyled Tailwind defaults.

### 2.1 The Sleek UI Token Architecture (HSL + CSS Custom Properties)
All visual tokens are defined as semantic CSS custom properties in HSL channels, decoupling color definitions from utility classes and enabling seamless light/dark mode and multi-tenant customization:

```css
:root {
  /* Sleek UI Precision Theme - Light Mode */
  --background: 0 0% 100%;
  --foreground: 0 0% 15%;
  --muted: 240 4.8% 95.9%;
  --muted-foreground: 240 3.8% 46.1%;
  --primary: 215 77% 47%;           /* High-precision signature blue / brand tint */
  --primary-foreground: 0 0% 100%;
  --secondary: 240 4.8% 95.9%;
  --secondary-foreground: 240 10% 3.9%;
  --accent: 240 4.8% 95.9%;
  --accent-foreground: 240 10% 3.9%;
  --destructive: 0 84.2% 60.2%;
  --destructive-foreground: 0 0% 100%;
  --border: 240 5.9% 90%;
  --input: 240 5.9% 90%;
  --ring: 215 77% 47%;
  --card: 0 0% 100%;
  --card-foreground: 240 10% 3.9%;
  --radius: 0.375rem;                /* Tight, crisp radius: default 6px */
}

.dark {
  /* Sleek UI Industrial Dark Mode - Deep Slate & Carbon Surfaces */
  --background: 0 0% 15%;            /* Deep precision dark canvas (#262626) */
  --foreground: 0 0% 95%;
  --muted: 240 33% 19%;
  --muted-foreground: 240 5% 64.9%;
  --primary: 215 77% 47%;
  --primary-foreground: 0 0% 100%;
  --secondary: 240 33% 19%;
  --secondary-foreground: 0 0% 95%;
  --accent: 240 33% 19%;
  --accent-foreground: 0 0% 95%;
  --destructive: 0 62.8% 30.6%;
  --destructive-foreground: 0 0% 100%;
  --border: 240 33% 22%;
  --input: 240 33% 22%;
  --ring: 215 77% 47%;
  --card: 240 33% 17%;               /* Elevated technical card surface */
  --card-foreground: 0 0% 95%;
}
```

- **Tailwind v4 `@theme inline` Integration**:
```css
@theme inline {
  --color-background: hsl(var(--background));
  --color-foreground: hsl(var(--foreground));
  --color-muted: hsl(var(--muted));
  --color-muted-foreground: hsl(var(--muted-foreground));
  --color-primary: hsl(var(--primary));
  --color-primary-foreground: hsl(var(--primary-foreground));
  --color-secondary: hsl(var(--secondary));
  --color-secondary-foreground: hsl(var(--secondary-foreground));
  --color-accent: hsl(var(--accent));
  --color-accent-foreground: hsl(var(--accent-foreground));
  --color-destructive: hsl(var(--destructive));
  --color-destructive-foreground: hsl(var(--destructive-foreground));
  --color-border: hsl(var(--border));
  --color-input: hsl(var(--input));
  --color-ring: hsl(var(--ring));
  --color-card: hsl(var(--card));
  --color-card-foreground: hsl(var(--card-foreground));
  --radius-sm: calc(var(--radius) - 2px);
  --radius-md: var(--radius);
  --radius-lg: calc(var(--radius) + 2px);
}
```

### 2.2 Dynamic Theme Passing & Multi-Tenant White-Labeling
Brand colors and design tokens must be passable dynamically from props, tenant databases, or user preferences:

1. **Brand Theme Configuration Schema**:
   ```typescript
   export interface BrandThemeConfig {
     primaryHueSat?: string;         // e.g. "215 77%" or custom brand tint
     primaryForeground?: string;     // e.g. "0 0% 100%"
     radius?: string;                // e.g. "0.375rem" (crisp) or "0.75rem" (rounded)
     darkBackground?: string;        // e.g. "0 0% 12%"
     accent?: string;                // e.g. "215 77% 47%"
     fontFamily?: string;            // e.g. "Inter, sans-serif"
   }
   ```

2. **React Dynamic Theme Provider**:
   ```tsx
   export function SleekThemeProvider({
     theme,
     children,
   }: {
     theme?: BrandThemeConfig;
     children: React.ReactNode;
   }) {
     const style = theme
       ? ({
           '--primary': theme.primaryHueSat ? `${theme.primaryHueSat} 47%` : undefined,
           '--ring': theme.primaryHueSat ? `${theme.primaryHueSat} 47%` : undefined,
           '--radius': theme.radius,
           '--primary-foreground': theme.primaryForeground,
         } as React.CSSProperties)
       : undefined;

     return (
       <div style={style} className="contents">
         {children}
       </div>
     );
   }
   ```

3. **Server-Side Tenant Styling (Next.js App Router)**:
   Inject customer brand overrides directly onto `<html>` or `<body>` in `layout.tsx`:
   ```tsx
   export default async function TenantLayout({ children }: { children: React.ReactNode }) {
     const tenant = await getTenantConfig();
     return (
       <html
         lang="en"
         style={{
           '--primary': tenant.primaryHsl,
           '--ring': tenant.primaryHsl,
           '--radius': tenant.cornerRadius ?? '0.375rem',
         } as React.CSSProperties}
       >
         <body className="bg-background text-foreground antialiased">{children}</body>
       </html>
     );
   }
   ```

### 2.3 Mobile Themed Primitives & Injected Tokens (React Native / Expo)
Synchronize tokens with mobile via `constants/Colors.ts` with explicit Sleek UI palettes:
```typescript
export interface SleekPalette {
  primary: string;
  primaryForeground: string;
  background: string;
  surface: string;
  card: string;
  text: string;
  textMuted: string;
  border: string;
  radius: number;
  alerts: {
    warning: string;
    success: string;
    error: string;
    info: string;
  };
}

export const sleekColors: { light: SleekPalette; dark: SleekPalette } = {
  light: {
    primary: '#1D63D2',             // Precision Blue (hsl 215 77% 47%)
    primaryForeground: '#FFFFFF',
    background: '#FFFFFF',
    surface: '#F4F4F6',
    card: '#FFFFFF',
    text: '#262626',
    textMuted: '#6B7280',
    border: '#E5E5EA',
    radius: 6,
    alerts: {
      warning: 'rgba(234, 179, 8, 0.9)',
      success: 'rgba(34, 197, 94, 0.9)',
      error: 'rgba(239, 68, 68, 0.9)',
      info: 'rgba(29, 99, 210, 0.9)',
    },
  },
  dark: {
    primary: '#1D63D2',
    primaryForeground: '#FFFFFF',
    background: '#262626',          // Sleek dark canvas (hsl 0 0% 15%)
    surface: '#1E1E28',
    card: '#1F1F2B',
    text: '#F2F2F2',
    textMuted: '#9CA3AF',
    border: '#2E2E3A',
    radius: 6,
    alerts: {
      warning: 'rgba(234, 179, 8, 0.9)',
      success: 'rgba(34, 197, 94, 0.9)',
      error: 'rgba(239, 68, 68, 0.9)',
      info: 'rgba(29, 99, 210, 0.9)',
    },
  },
};
```
- Custom tenant mobile apps pass brand overrides into `ThemeProvider`: `<ThemeProvider brandOverrides={tenantTheme}>`.

### 2.4 Sleek UI Component Standards
Every core UI component must reflect precision industrial craftsmanship:

- **Buttons**:
  - `primary`: `bg-primary text-primary-foreground font-semibold px-4 py-2 rounded-[var(--radius)] hover:opacity-95 active:scale-[0.98] transition-all shadow-sm`.
  - `secondary`: `bg-secondary text-secondary-foreground font-medium px-4 py-2 rounded-[var(--radius)] hover:bg-secondary/80 active:scale-[0.98] transition-all`.
  - `ghost`: `bg-transparent text-foreground hover:bg-muted font-medium px-4 py-2 rounded-[var(--radius)] transition-colors`.
  - `outline`: `border border-border text-foreground bg-transparent hover:bg-muted font-medium px-4 py-2 rounded-[var(--radius)] transition-colors`.

- **Cards & Surface Containers**:
  - `bg-card text-card-foreground border border-border rounded-lg p-6 shadow-sm hover:shadow-md transition-shadow`.
  - In dark mode: `border-border/60 bg-card/95 backdrop-blur-sm`.

- **Form Controls & Inputs**:
  - `bg-background text-foreground border border-input rounded-[var(--radius)] px-3 py-2 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 transition-all`.

- **Badges**:
  - Crisp, technical, and compact: `inline-flex items-center rounded-sm px-2.5 py-0.5 text-xs font-semibold tracking-wide`.
  - Variants: `default` (`bg-primary text-primary-foreground`), `secondary` (`bg-secondary text-secondary-foreground`), `outline` (`border border-border text-foreground`).

### 2.5 Industrial Aesthetics, Lighting & Glassmorphism
Produce modern, premium visual depth without heavy image assets using parameterized tokens:
- **Atmospheric Hero Glow (`.hero-glow`)**:
  ```css
  .hero-glow {
    background:
      radial-gradient(ellipse 80% 60% at 70% 20%, hsl(var(--primary) / 0.25), transparent),
      radial-gradient(ellipse 60% 50% at 20% 80%, hsl(var(--accent) / 0.15), transparent),
      linear-gradient(160deg, #18181b 0%, #09090b 100%);
  }
  ```
- **Ambient Mockup Halo Glow**: Place an ambient blurred sphere behind device mockups:
  ```tsx
  <div className="absolute inset-0 rounded-full bg-primary/20 blur-3xl pointer-events-none" />
  ```
- **Gradient Brand Utility (`.gradient-brand`)**:
  `background: linear-gradient(135deg, hsl(var(--primary)) 0%, hsl(var(--accent)) 100%);`
- **Gradient Headline Accent (`.gradient-text`)**:
  ```css
  .gradient-text {
    background: linear-gradient(135deg, #60a5fa 0%, hsl(var(--primary)) 50%, #93c5fd 100%);
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
  }
  ```
- **Glassmorphism (`.glass-card`)**:
  ```css
  .glass-card {
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.1);
    backdrop-filter: blur(12px);
  }
  ```

### 2.6 Typography Hierarchy & Accessibility (WCAG 2.1 AA)
- **Font Families**: Sans (`Inter`, `system-ui`), Mono (`JetBrains Mono`, monospace), Serif (`Georgia`, serif).
- **Contrast Target**: Minimum 4.5:1 contrast ratio across all text elements (test both light and dark modes).
- **Focus Rings**: `2px solid hsl(var(--ring))` with `2px` offset (`focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2`).
- **Section Labels (`.section-label`)**: Uppercase, tracked wide, and tinted with the primary token:
  `@apply text-xs font-semibold uppercase tracking-widest text-primary;`
- **Headings**:
  - Hero Title: `text-4xl font-bold leading-[1.08] tracking-tight text-white md:text-6xl lg:text-7xl`.
  - Section Headings: `text-3xl font-bold tracking-tight md:text-4xl text-foreground`.

### 2.7 Micro-Interactions & Fluid Transitions
- **Hover & Active States**: Subtle, tactile scaling: `transition-transform duration-200 hover:scale-[1.01] active:scale-[0.98]`.
- **Text Entrance Animation**:
  ```css
  @keyframes step-text-in {
    from { opacity: 0; transform: translateY(10px); }
    to { opacity: 1; transform: translateY(0); }
  }
  .step-text-in { animation: step-text-in 0.45s cubic-bezier(0.16, 1, 0.3, 1); }
  ```
- **Respect Reduced Motion**: Always wrap continuous animations or transforms with `@media (prefers-reduced-motion: reduce)` or Tailwind's `motion-reduce:*` variants.

---

## 3. Product & Marketing Landing Pages (Next.js App Router)

### 3.1 Strict Purpose & Guardrails
- Marketing and product pages are lightweight, lightning-fast, and SEO-optimized.
- **Zero Scope Creep**: Do NOT add authentication, user dashboards, or complex transactional business logic directly into a marketing site. Redirect users to the authenticated application/dashboard instead.
- Default to **React Server Components (RSC)**; use `'use client'` strictly on interactive leaf components (e.g., interactive walkthroughs, carousels, mobile navigation drawers).

### 3.2 High-Converting Section Hierarchy
Every product or landing page must follow this structured visual hierarchy:
1. `<Header />`: Sticky header with background blur, brand logo (`<BrandMark />`), section navigation anchors (`#features`, `#how-it-works`, `#solutions`, `#pricing`), and a high-contrast pill CTA.
2. `<Hero />`:
   - Category eyebrow tag (`text-sm font-semibold uppercase tracking-wider text-brand-primary`).
   - High-impact `<h1>` headline with `.gradient-text` accent.
   - Concise value proposition subtext (max 2 sentences).
   - Primary and secondary conversion CTAs or app store badges (`<StoreBadges />`).
   - Responsive device preview (`<MockDevice priority />`) paired with an ambient halo backdrop (`bg-brand-primary/20 blur-3xl`).
3. `<ValueNarrative />`: Two-column grid detailing the problem and value proposition, accompanied by feature highlights.
4. `<PersonaValueGrids />`: Value propositions split by persona or use case (e.g., Individuals vs. Teams / Clients vs. Administrators):
   - Structured in responsive multi-column grids (`grid gap-6 md:grid-cols-3`).
   - Alternates between clean light theme (`bg-brand-surface-subtle`, white cards with `.gradient-brand` top accent borders) and dark atmospheric theme (`.hero-glow` with `.glass-card`).
5. `<HowItWorks />`: 3 to 5 step visual workflow featuring auto-advancing carousels or interactive tabs, perspective device previews, step numbers, and progress indicators.
6. `<ConversionCTA />`: Final conversion banner with clear value statement, download copy, store badges, or signup CTA.
7. `<Footer />`: Semantic navigation, copyright, legal links, and social channels.

### 3.3 `<MockDevice>` Component Standards
All device previews (phones, tablets, browser mockups) must use standardized `<MockDevice>` wrappers (`next/image`):
- Explicit dimensions preventing layout shifts:
  - Mobile Portrait: `1419x2796` (constraint: `max-w-[260px] lg:max-w-[300px]`).
  - Mobile Perspective / Tilted: `1857x3096` (constraint: `max-w-[300px] lg:max-w-[360px]`).
  - Desktop / Browser Window: `16:10` aspect ratio with browser top-bar chrome.
- Always pair with `drop-shadow-2xl` and relative z-indexing.
- Above-the-fold hero mockups **must** include the `priority` attribute for optimal Largest Contentful Paint (LCP).

### 3.4 Interactive Walkthrough Patterns (`HowItWorks.tsx`)
- Auto-rotates or allows manual tab switching across steps, synchronizing the active step index.
- Cross-fades visual assets between inactive state and highlighted active state.
- Inactive steps scale down subtly (`scale-[0.8] opacity-50 hover:opacity-75`) while active step scales up (`scale-100 opacity-100 z-10`).
- Progress indicator pills: Active pill smoothly expands to `w-8 gradient-brand`; inactive pills remain `w-2 bg-brand-border`.

### 3.5 Conversion Badges & Action CTAs
- **Store Badges (`<StoreBadge />`)**:
  - Render SVG icons with dual-tier typography: small 10px uppercase subtext + 14px bold title.
  - Support both `dark` (`bg-white text-brand-dark`) and `light` (`bg-brand-dark text-white`) variants with `hover:scale-[1.02]`.
- **Pill Buttons (`<PillButton />`)**:
  - Rounded full buttons with hover translation and focus-visible rings.

### 3.6 SEO, Core Web Vitals & Metadata
- Every page must export comprehensive metadata from `layout.tsx` or `page.tsx` (`title`, `description`, `openGraph`, `twitter`, `alternates`, `robots`).
- Use semantic HTML tags: `<header>`, `<main>`, `<section id="...">`, `<article>`, `<nav>`, `<footer>`.
- Optimize Core Web Vitals: Preload critical web fonts with `next/font`, configure `sizes` on all responsive images, and guarantee 0 Cumulative Layout Shift (CLS).

---

## 4. Cross-Platform Mobile Standards (React Native / Expo)

### 4.1 Root Multi-Provider Hierarchy (`app/_layout.tsx`)
Every Expo application must configure providers in this exact nested order:
```tsx
<GestureHandlerRootView style={{ flex: 1 }}>
  <AuthProvider>
    <DataProvider>
      <BottomSheetModalProvider>
        <MasterBottomSheetsProvider>
          <ThemeProvider brandPalette={configuredBrandPalette} colorScheme={colorScheme}>
            <AuthProtection>
              <Stack screenOptions={{ headerShown: false }}>
                <Stack.Screen name="(tabs)" />
                <Stack.Screen name="auth" />
              </Stack>
            </AuthProtection>
          </ThemeProvider>
        </MasterBottomSheetsProvider>
      </BottomSheetModalProvider>
    </DataProvider>
  </AuthProvider>
</GestureHandlerRootView>
```
- **Auth Guard**: Centralize auth routing in `AuthProtection` using `useSegments()` and `useAuth()`. Automatically redirect unauthenticated users to `/auth/sign-in` and authenticated users away from public auth screens. Never duplicate auth checks inside screens.
- **Splash Screen**: Prevent auto-hiding with `SplashScreen.preventAutoHideAsync()`. Only hide once custom fonts and critical cache items have loaded.

### 4.2 Themed UI Primitives & Injected Tokens
Never hardcode hex values or use raw React Native `<Text>` / `<View>` directly:
- **`ThemedText`**: Accepts `type` prop (`'default' | 'defaultSemiBold' | 'title' | 'subtitle' | 'link' | 'caption'`) and dynamically computes colors via `useThemeColor`.
- **`ThemedView`**: Accepts `variant` prop (`'background' | 'surface' | 'card'`) and dynamically resolves container surface colors for light/dark themes.
- **`useThemeColor({ light, dark }, colorName)`**: Central hook for resolving colors from active theme tokens and user theme preferences.

### 4.3 UI Component System: Variants & Sizes Pattern
All reusable controls (`components/ui/`) must implement the `variant` + `size` pattern:
- **`Button.tsx`**:
  - `variant`: `'primary' | 'secondary' | 'outline' | 'ghost'`.
  - `size`: `'sm' (8/16 pad, 13 font)` | `'md' (12/20 pad, 14 font)` | `'lg' (16/28 pad, 16 font)`.
  - Implement size/color styling via helper lookup functions (`getSizeStyles()`) rather than multiple boolean flags.
  - Supports loading state (`<ActivityIndicator color={variant === 'primary' ? '#fff' : palette.primary} />`), left/right icons, and disabled states.
- **Cards (`Card.tsx`, `EntityCard.tsx`)**:
  - Rounded corners (16-20px), soft elevation (`shadowColor: '#000', shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.08, shadowRadius: 8, elevation: 2`).
  - Active press feedback with `activeOpacity={0.7}`.
  - Clear information hierarchy: Title, badge/tag, metadata row, and primary action CTA.
- **Filter Chips (`Chip.tsx`)**:
  - Rounded pill shape (`borderRadius: 20`, padding `8px 4px`).
  - Horizontal scroll with hidden scroll indicators (`showsHorizontalScrollIndicator={false}`).
  - Active state switches to active brand tint with an animated checkmark icon.

### 4.4 Role-Based UI via Composition
Render persona-specific interfaces using `RoleBasedLayout` and `RoleBasedRenderer`:
- Pass role views as props: `adminComponent`, `clientComponent`, `guestComponent`.
- Let the layout centrally handle user role switching (`user.role`), headers, safe area insets, and notification badges (`useNotificationBadge()`).

### 4.5 Modals & Bottom Sheets
- Standardize all modal interactions on `@gorhom/bottom-sheet`.
- Use `BaseBottomSheetModal.tsx` for shared presentation (backdrop blur, handle indicator, snap points).
- Register modal dialogs in `MasterBottomSheetsProvider` to allow triggering sheets cleanly via refs/context without cluttering screen state.

### 4.6 Mobile Optimization & Native Performance
- **High-Performance Images**: Always use `expo-image` (`<Image priority={...} />`), never React Native's default `<Image>`.
- **List Virtualization**: Use `@shopify/flash-list` or optimized `FlatList` with `getItemLayout` and stable `keyExtractor` for long feeds; never use un-virtualized `ScrollView` for unbounded lists.
- **Safe Areas**: Wrap top-level screens using `react-native-safe-area-context` (`SafeAreaView`, `edges={['top']}`).
- **Touch Targets**: Ensure minimum 44px tap targets for buttons and interactive controls (WCAG compliance).
- **Haptics**: Trigger `expo-haptics` (`Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light)`) on tab switches and primary user actions.
- **Skeletons**: Use shared skeleton bones (`components/skeletons/bone.ts`) and modular skeleton cards instead of full-screen blocking spinners.
- **Platform Overrides**: Use the `.web.ts` suffix convention (e.g., `useColorScheme.web.ts`) when web platform behavior diverges from native.

### 4.7 API Client Singleton (`services/api.ts`)
- Maintain a centralized `ApiService` singleton managing JWT persistence via `@react-native-async-storage/async-storage`.
- Normalize all responses using `normalizeResponse<T>()` and `isApiSuccess()`, strictly conforming to the standard `{ status: 'success', data }` contract.
- Use SWR or TanStack Query for client data fetching, caching, and background revalidation.
- Use Formik or React Hook Form with Yup/Zod for form state management and schema validation.

---

## 5. Web Application & Dashboard Standards (Next.js App Router)

### 5.1 Architecture & Route Protection
- Route groups organized under `app/`:
  - `(dashboard)/`: Authenticated administration and application workflows.
  - `(auth)/`: Authentication flows (sign-in, registration, password recovery).
  - `(account)/` or `(settings)/`: Profile, organization, and preferences management.
- **Middleware Authentication (`middleware.ts`)**:
  - Centralize session validation and route guarding in Edge middleware.
  - Validate session tokens (`accessToken`, `session_token`, or tenant cookie).
  - Redirect unauthenticated requests targeting protected routes (`/dashboard/*`) to `/auth/signin?callbackUrl=...`.
  - Redirect authenticated users visiting public auth pages to their appropriate dashboard home (`/dashboard/admin` for administrators, `/dashboard` for standard users).

### 5.2 Dashboard Layout & Sidebar Standard
- **Sidebar Width**: Standard dashboard navigation (`DashboardSidebar.tsx`) has a fixed standard width: **`w-64`**.
  ```tsx
  className="fixed inset-y-0 left-0 z-40 w-64 bg-brand-surface border-r border-brand-border transform transition-transform duration-300 ease-in-out"
  ```
- **Mobile Sidebar Toggle Pattern**:
  - Implement a lightweight, decoupled toggle using either window custom event dispatch or a minimal layout state context:
    `window.dispatchEvent(new Event('toggle-sidebar'))`
  - In `DashboardSidebar`:
    `window.addEventListener('toggle-sidebar', handler)`
  - Do NOT introduce heavy multi-level prop-drilling or large global stores for simple navigation toggles.
- **Active Navigation Item Styling**:
  - Exact match on dashboard home (`pathname === item.href`).
  - Prefix match on nested sub-routes (`pathname.startsWith(item.href + '/')`).
  - Active class: `bg-brand-primary text-white shadow-sm`. Inactive class: `text-brand-text hover:bg-brand-primary/10 transition-colors`.

### 5.3 Server Components (RSC) vs. Client Components
- Default to **React Server Components (RSC)** for data fetching, static layouts, permission checks, and page skeletons.
- Place `'use client'` strictly at interactive leaf boundaries (forms, dropdown menus, filter inputs, modal dialogs).
- Never push `'use client'` to the top of an entire page if only a single child component requires client-side state.

### 5.4 State Management, Forms, SWR & Skeletons
- Use SWR or TanStack Query for client-side data caching, optimistic updates, and background revalidation.
- Use Formik or React Hook Form paired with Zod or Yup for declarative form validation schemas.
- Provide comprehensive skeleton fallbacks in `components/skeletons/` matching the actual layout geometry to avoid visual jumping during async Suspense transitions.

---

## 6. Enterprise Backend API Standards (NestJS & Node.js)

### 6.1 Architecture & Domain Module Structure
Every feature domain resides within an isolated module directory under `src/<feature>/`:
- `<feature>.module.ts`: Wires controller, service, TypeORM/Prisma repositories, and feature dependencies into `app.module.ts`.
- `<feature>.controller.ts`: Thin HTTP routing layer decorated with OpenAPI annotations (`@ApiTags()`, `@ApiOperation()`). Handles route matching, guards, parameter extraction, and DTO validation. **Zero business logic or raw database queries.**
- `<feature>.service.ts`: Houses all domain logic, business rules, and repository transactions. Must extend `BaseCrudService<T>` for standard CRUD entities.
- `<feature>.entity.ts`: Database entity extending `BaseEntity`.
- `dto/`: Input/output contracts decorated with `class-validator`, `class-transformer`, and `@ApiProperty()`.
- `<feature>.subscriber.ts` or `<feature>.processor.ts` (optional): Asynchronous event subscribers or queue workers for decoupled side-effects.

### 6.2 DTO Naming & Validation Rules
- **File Naming**: Always kebab-case with `.dto.ts` suffix (e.g., `create-resource.dto.ts`, `update-resource.dto.ts`, `filter-query.dto.ts`).
- **Class Naming**: Always PascalCase ending in `DTO` (e.g., `CreateResourceDTO`, `UpdateResourceDTO`, `FilterQueryDTO`).
- **Property Naming**: Follow the workspace contract consistently (e.g., snake_case for external REST payloads, camelCase for internal TypeScript entities).
- **Validation Decorators**: Every field must have explicit `class-validator` decorators with clear user-facing messages:
  ```typescript
  export class CreateResourceDTO {
    @ApiProperty({ description: 'The unique resource name' })
    @IsNotEmpty({ message: 'Name is required' })
    @IsString({ message: 'Name must be a string' })
    name: string;

    @ApiPropertyOptional({ description: 'Contact email' })
    @IsOptional()
    @IsEmail({}, { message: 'Must be a valid email address' })
    email?: string;

    @ApiProperty({ description: 'Associated organization ID' })
    @IsUUID(undefined, { message: 'organization_id must be a valid UUID' })
    organization_id: string;

    @ApiProperty({ description: 'Scheduled ISO 8601 timestamp' })
    @IsDateString({}, { message: 'scheduled_at must be a valid ISO 8601 date string' })
    scheduled_at: string;
  }
  ```
- **Nested Object / Array Validation**: Always combine `@ValidateNested({ each: true })` with `@Type(() => NestedDTO)`:
  ```typescript
  @ApiProperty({ type: [ResourceItemDTO] })
  @IsArray({ message: 'items must be an array' })
  @ValidateNested({ each: true })
  @Type(() => ResourceItemDTO)
  items: ResourceItemDTO[];
  ```

### 6.3 Base Entity & CRUD Inheritance (`src/common/`)
- All database entities must extend `BaseEntity` (`src/common/entities/base.entity.ts`):
  ```typescript
  export abstract class BaseEntity {
    @PrimaryGeneratedColumn('uuid')
    id: string;

    @Column({ type: 'jsonb', nullable: true })
    metadata?: Record<string, unknown> | null;

    @CreateDateColumn({ type: 'timestamptz' })
    createdAt: Date;

    @UpdateDateColumn({ type: 'timestamptz' })
    updatedAt: Date;

    @DeleteDateColumn({ type: 'timestamptz', nullable: true })
    deletedAt?: Date; // Enables soft deletion
  }
  ```
- Services performing standard CRUD must extend `BaseCrudService<T extends BaseEntity>` (`src/common/crud/crud.service.ts`):
  ```typescript
  @Injectable()
  export class ResourceService extends BaseCrudService<ResourceEntity> {
    constructor(
      @InjectRepository(ResourceEntity)
      private readonly resourceRepo: Repository<ResourceEntity>,
    ) {
      super(resourceRepo);
    }
  }
  ```
  - Standard methods provided: `create`, `findAll`, `findOne`, `findByStringId`, `findByWhere`, `findManyWithPagination`, `findOneByWhere`.
  - Standard Pagination Contract: Returns `IPagination<T>` (`items: T[]`, `meta: { total: number, page: number, limit: number, hasNextPage: boolean }`), defaulting to `take: 10`, `skip: 0`.

### 6.4 Response & Error Standards (RFC 7807 Alignment)
- Standardize all API responses via response formatting helpers or a global `TransformInterceptor`:
  ```typescript
  // Successful response envelope:
  // { status: 'success', data: T, meta?: IPaginationMeta }
  return formatResponse(await this.resourceService.createResource(entity, userId));
  ```
- Standardize error responses following RFC 7807 problem details:
  ```typescript
  // Error response envelope:
  // { status: 'failed', error: { code: string, message: string, details?: unknown } }
  try {
    return formatResponse(await this.service.execute());
  } catch (error) {
    return formatErrorResponse(error);
  }
  ```
- Throw standard NestJS `HttpException` subclasses (`NotFoundException`, `BadRequestException`, `UnauthorizedException`, `ForbiddenException`, `ConflictException`), never throw generic `Error`.

### 6.5 Request Context, Tracing & Authentication
- **CLS / AsyncLocalStorage Context**: Wrap requests in `RequestContextMiddleware` to store request-scoped data.
- Access caller identity and correlation IDs anywhere in the execution path without prop-drilling:
  ```typescript
  const userId = RequestContext.getCurrentUserId();
  const correlationId = RequestContext.getCorrelationId();
  const tenantId = RequestContext.getTenantId();
  ```
- **Guarded Endpoints**:
  ```typescript
  @Controller('resources')
  @UseGuards(JwtAuthGuard, RolesGuard)
  export class ResourceController {
    @Post()
    @UserRoles(RolesEnum.ADMIN)
    async create(@Body() dto: CreateResourceDTO) { ... }
  }
  ```
- **Public Routes**: Explicitly decorate unauthenticated endpoints with `@Public()`.
- Passwords and sensitive authentication tokens must always be hashed with `bcrypt`/`argon2` (salt rounds >= 10).

### 6.6 Asynchronous Side Effects & Queue Processing
- Use TypeORM `@EventSubscriber()` or event emitters to decouple non-blocking side effects (e.g., audit logging, email notifications, search index sync) from primary database writes.
- For heavy or failure-prone tasks (e.g., document processing, batch imports), delegate to background queues (BullMQ, Redis).
- Transactional integrity: When mutating multiple entities, wrap operations in database transactions (`queryRunner` or `dataSource.transaction()`).

### 6.7 AI / LLM / Vector Isolation Standards
- LLM and embedding logic (LangChain, OpenAI, Gemini, pgvector) must reside strictly in dedicated feature modules (e.g., `ai/`, `agents/`, `embeddings/`).
- Never inline raw prompt templates or direct model calls inside standard CRUD services.
- Always validate and sanitize both user inputs to prompts and LLM-generated JSON outputs before executing downstream operations.

---

## 7. Enterprise Monorepo, Testing & CI/CD Standards

### 7.1 Monorepo Boundaries & Module Decoupling
- Enforce strict architectural boundaries (via `@nx/enforce-module-boundaries` or eslint-plugin-import).
- Applications must depend on shared, buildable packages (e.g., `@workspace/ui`, `@workspace/utils`, `@workspace/types`, `@workspace/api-client`), never reaching directly into sibling application directories.
- Circular dependencies between modules, libraries, or packages are strictly forbidden.

### 7.2 Strict Unit Testing Standard (AAA Pattern)
Every unit test must strictly adhere to the **Arrange → Act → Assert** pattern:
- **Colocation**: Test files must live immediately beside the source file: `FileName.spec.ts` or `FileName.spec.tsx`.
- **Target Coverage**: Maintain high code coverage (~100% statement, line, and branch coverage for new and modified business logic).
- **Mock Lifecycle**:
  - Mock external dependencies at the file root using `jest.mock('module', () => ({ ... }))` or `vi.mock(...)`.
  - Always reset and clear mocks in `beforeEach(() => { jest.clearAllMocks(); })`.
  - For dynamic mock access inside test bodies, **always** use `jest.requireMock('module')` — **NEVER** use `require('module')` which triggers `@typescript-eslint/no-var-requires`.
- **React Testing Library Standards**:
  - Always import `@testing-library/jest-dom` at the top of component test files.
  - Query by accessibility role (`getByRole`, `findByRole`) or accessible label first; fall back to `getByTestId` only when necessary.
  - Assert on user-visible rendered text or DOM presence rather than internal component state.

### 7.3 Git Conventions & Semantic Commits
- **Conventional Commits**: Format commit messages according to the Conventional Commits specification:
  `<type>(<scope>): <short description>`
  - Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`.
- **Branch Naming**:
  `^(feature|bugfix|hotfix|chore|release|refactor|revert)/[a-z0-9._-]+$`
  - Format: `<type>/<ticket-or-scope>-<short-description>` (e.g., `feature/auth-refresh-token`).
- **Pre-Commit Verification**: Run local linting, type checks, and affected tests before pushing code.

### 7.4 CI/CD Quality Gates & Security Scans
- Automated CI pipeline must pass the following quality gates before merging:
  1. Strict TypeScript compilation (`tsc --noEmit`).
  2. Linter execution with zero errors (`eslint`).
  3. Formatter compliance (`prettier --check`).
  4. Unit and integration test suites (`jest` / `vitest`).
  5. Dependency security vulnerability scan (`npm audit` / Snyk).

---

## 8. Universal Code Quality Checklist

Before committing code or completing any task, every autonomous agent and software engineer must verify this checklist:

- [ ] **DRY & Reusability**: Is there any duplicate logic, styling, database query, or navigation structure that should be extracted into a shared helper or component?
- [ ] **Composition**: Are UI variants composed from modular primitives rather than large monolithic conditionals?
- [ ] **Inheritance**: In backend services, do CRUD services extend `BaseCrudService<T>` and database entities extend `BaseEntity`?
- [ ] **DTO Discipline**: Are all DTOs named `*DTO`, files named `*.dto.ts`, properties formatted consistently, and fields decorated with `class-validator` and `@ApiProperty()`?
- [ ] **Auth Context**: Is authenticated user access resolved via request context (`RequestContext.getCurrentUserId()`) and guarded with `JwtAuthGuard` + `RolesGuard`?
- [ ] **Response Standard**: Are controller responses formatted using `formatResponse()` and `formatErrorResponse()` following standardized envelopes?
- [ ] **Sidebar Standard**: In web dashboards, does the sidebar strictly use the standard `w-64` width and accessible toggle event?
- [ ] **Token Discipline**: Are colors and themes referenced through semantic design tokens (`globals.css`, HSL CSS variables, or `Colors.ts`) rather than hardcoded hex values?
- [ ] **Configurable Theming**: Can brand colors and tokens be passed dynamically via `ThemeProvider` or CSS variables for custom branding / multi-tenancy?
- [ ] **Sleek UI Standards**: Do front-end interfaces embody Sleek UI precision engineering (crisp `0.375rem` radius, tactile micro-interactions, atmospheric hero glow, glassmorphism, and responsive `<MockDevice>` previews)?
- [ ] **Accessibility (a11y)**: Does the UI meet WCAG 2.1 AA standards (4.5:1 text contrast ratio, 2px focus rings with offset, minimum 44px tap targets, reduced motion support)?
- [ ] **Comment Discipline**: Are all comments strictly one short line explaining the non-obvious *why*? Are all commented-out code blocks deleted?
- [ ] **Concise Code**: Are functions small (< 150 lines), using early returns and minimal nesting?
- [ ] **Type Safety & Lint**: Does the code compile with 0 lint errors (`npm run lint`) and 0 type errors (`tsc --noEmit`)?
- [ ] **Testing**: Do all new or modified tests follow the AAA pattern with mocks cleared in `beforeEach` and high branch/statement coverage?
- [ ] **Zero Secrets & Logs**: Are all credentials loaded via environment config and all ad-hoc `console.log` statements removed?

