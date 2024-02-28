/** @type {import('./$types').PageServerLoad} */
export async function load({ fetch }) {
    const response = await fetch(`/api/v1/agents`);
    const data = await response.json();
    return { data };
    }