/** @type {import('./$types').PageServerLoad} */
export async function load({ fetch }) {
    // Get tasks data
    const tasks = await fetch(`/api/v1/tasks`);
    const tasks_data = await tasks.json();
    // Get jobs data
    const jobs = await fetch(`/api/v1/jobs`);
    const jobs_data = await jobs.json();
    // Combine data
    const data = {
        tasks: tasks_data,
        jobs: jobs_data
    };
    return { data };
}