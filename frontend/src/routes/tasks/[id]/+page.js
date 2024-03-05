import { error } from "@sveltejs/kit";
/** @type {import('./$types').PageLoad} */
export const load = async ({ params, data }) => {
    const { id } = params;
    // Get the entry from data[] where _id matches id
    const task_data = data.data.find((d) => d._id === id);
    return { task_data } || error(404, "Not found");
    };