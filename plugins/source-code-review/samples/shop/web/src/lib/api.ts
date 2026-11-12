export * from './format';
export const api = (path: string) => fetch(`/api${path}`);
