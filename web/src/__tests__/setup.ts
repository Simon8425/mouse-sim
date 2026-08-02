import '@testing-library/jest-dom';

// Global mocks for canvas / WebGL / ResizeObserver if needed in jsdom
if (typeof window !== 'undefined') {
  class ResizeObserverMock {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  window.ResizeObserver = ResizeObserverMock;
}
