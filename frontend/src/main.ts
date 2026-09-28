import { mount } from "svelte";
import "./app.css";
import App from "./App.svelte";
import { boot } from "$lib/app.svelte";

const app = mount(App, { target: document.getElementById("app")! });
boot();

export default app;
