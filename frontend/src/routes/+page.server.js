/** @type {import('./$types').PageServerLoad} */
export async function load({ fetch }) {
    const response = await fetch(`/api/v1/tasks`);
    const data = await response.json();
    return { data };
    }