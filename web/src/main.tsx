import * as React from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource-variable/inter';
import './theme.css';
import './styles.css';
import { ProjectProvider } from './state/projectStore';
import { App } from './App';

const rootElement = document.getElementById('root');
if (rootElement === null) {
  throw new Error('missing #root element');
}
createRoot(rootElement).render(
  <React.StrictMode>
    <ProjectProvider>
      <App />
    </ProjectProvider>
  </React.StrictMode>,
);
