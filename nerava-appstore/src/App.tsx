/**
 * Top-level application — owns the state store, renders the phone
 * frame, swaps between screens, and renders the bottom tab bar
 * overlay. Pure state-driven navigation — no router, no history.
 */

import { BottomTabBar } from "./components/BottomTabBar.js";
import { DetailScreen } from "./screens/DetailScreen.js";
import { HomeScreen } from "./screens/HomeScreen.js";
import { MyAppsScreen } from "./screens/MyAppsScreen.js";
import { SettingsScreen } from "./screens/SettingsScreen.js";
import { useAppStoreState } from "./state/store.js";

export default function App(): React.JSX.Element {
  const store = useAppStoreState();

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <div className="phone-frame">
        <ScreenRouter store={store} />
        <BottomTabBar active={store.activeScreen} onSelect={store.navigateTo} />
      </div>
    </div>
  );
}

function ScreenRouter({
  store,
}: {
  readonly store: ReturnType<typeof useAppStoreState>;
}): React.JSX.Element {
  switch (store.activeScreen) {
    case "home":
      return <HomeScreen store={store} />;
    case "detail": {
      // Defensive: if selectedApp is somehow null, fall back to home.
      if (store.selectedApp === null) {
        return <HomeScreen store={store} />;
      }
      return <DetailScreen store={store} app={store.selectedApp} />;
    }
    case "myApps":
      return <MyAppsScreen store={store} />;
    case "settings":
      return <SettingsScreen />;
  }
}
