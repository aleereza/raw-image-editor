import { mount } from "svelte";
import Review from "./Review.svelte";
import "../app.css";

const target = document.getElementById("app");
if (!target) throw new Error("no #app element");
export default mount(Review, { target });
