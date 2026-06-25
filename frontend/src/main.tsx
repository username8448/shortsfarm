import React from 'react';
import {createRoot} from 'react-dom/client';
import {App} from './App';
import './styles.css';

const embeddedRoot = document.getElementById('studio-root');
const target = embeddedRoot || document.getElementById('root');

if (!target) {
  throw new Error('Template Studio mount point не найден.');
}

createRoot(target).render(
  <React.StrictMode>
    <App embedded={Boolean(embeddedRoot)} />
  </React.StrictMode>,
);
