import asyncio
import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from core.config import load_config, AppConfig, generate_example_config

console = Console()
logger = logging.getLogger(__name__)


def setup_logging(config: AppConfig) -> None:
    log_path = Path(config.notification.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )


@click.group()
@click.option("--config", "-c", "config_path", type=click.Path(exists=True), help="Path to config file")
@click.pass_context
def cli(ctx, config_path):
    """Snap Buy - Automated purchase tool for coding plans."""
    ctx.ensure_object(dict)
    if config_path:
        try:
            ctx.obj["config"] = load_config(Path(config_path))
        except Exception as e:
            console.print(f"[red]Error loading config: {e}[/red]")
            sys.exit(1)
    else:
        config_file = Path(__file__).resolve().parent / "config.yaml"
        if config_file.exists():
            ctx.obj["config"] = load_config(config_file)
        else:
            console.print("[yellow]No config.yaml found. Using defaults.[/yellow]")
            ctx.obj["config"] = AppConfig()


@cli.command()
@click.option("--now", "run_now", is_flag=True, help="Run immediately without scheduling")
@click.option("--debug", "debug_mode", is_flag=True, help="Enable debug mode: screenshots + HTML dumps")
@click.pass_context
def run(ctx, run_now, debug_mode):
    """Run the purchase automation at scheduled times."""
    config = ctx.obj["config"]
    if debug_mode:
        config.browser.debug = True
    setup_logging(config)
    if debug_mode:
        logging.getLogger().setLevel(logging.DEBUG)
    asyncio.run(_run_scheduler(config, run_now=run_now))


async def _run_scheduler(config: AppConfig, run_now: bool = False):
    from core.browser import BrowserManager
    from core.notifier import Notifier
    from core.scheduler import PurchaseScheduler
    from core.retry import RetryConfig
    from platforms.aliyun.buyer import AliyunBuyer
    from platforms.glm.buyer import GLMBuyer

    notifier = Notifier(config.notification)
    notifier.info("Snap Buy starting...")

    browser_manager = BrowserManager(config.browser)
    await browser_manager.launch()

    scheduler = PurchaseScheduler(config)
    scheduler.set_notifier(notifier)

    try:
        buyers = {}

        if config.platforms.aliyun.enabled:
            aliyun_config = config.platforms.aliyun
            aliyun_buyer = AliyunBuyer(
                config=aliyun_config,
                browser_manager=browser_manager,
                notifier=notifier,
                retry_config=RetryConfig(max_retries=aliyun_config.max_retries),
            )
            buyers["aliyun"] = (aliyun_buyer, aliyun_config)

            async def aliyun_job(pre_warm=False, context=None):
                if pre_warm:
                    return await aliyun_buyer.pre_warm()
                return await aliyun_buyer.run(context=context)

            scheduler.schedule_platform("aliyun", aliyun_config.purchase_time, aliyun_job)

        if config.platforms.glm.enabled:
            glm_config = config.platforms.glm
            glm_buyer = GLMBuyer(
                config=glm_config,
                browser_manager=browser_manager,
                notifier=notifier,
                retry_config=RetryConfig(max_retries=glm_config.max_retries),
            )
            buyers["glm"] = (glm_buyer, glm_config)

            async def glm_job(pre_warm=False, context=None):
                if pre_warm:
                    return await glm_buyer.pre_warm()
                return await glm_buyer.run(context=context)

            scheduler.schedule_platform("glm", glm_config.purchase_time, glm_job)

        if run_now:
            # --now mode: run all enabled platforms immediately
            for platform_name, (b, plat_config) in buyers.items():
                console.print(f"[cyan]Running {platform_name} purchase immediately...[/cyan]")

                async def make_job(buyer=b):
                    async def job(pre_warm=False, context=None):
                        if pre_warm:
                            return await buyer.pre_warm()
                        return await buyer.run(context=context)
                    return job

                await scheduler.run_immediate(await make_job())
        else:
            scheduler.start()
            console.print("[green]Scheduler running. Press Ctrl+C to stop.[/green]")
            try:
                while True:
                    await asyncio.sleep(3600)
            except (KeyboardInterrupt, asyncio.CancelledError):
                pass
    finally:
        scheduler.stop()
        await browser_manager.close()
        notifier.info("Snap Buy stopped")


@cli.command()
@click.argument("platform", type=click.Choice(["aliyun", "glm"]))
@click.pass_context
def login(ctx, platform):
    """Perform manual login to save session state."""
    config = ctx.obj["config"]
    setup_logging(config)
    asyncio.run(_login(config, platform))


async def _login(config: AppConfig, platform: str):
    from core.browser import BrowserManager
    from core.notifier import Notifier

    browser_manager = BrowserManager(config.browser)
    await browser_manager.launch()

    try:
        if platform == "aliyun":
            from platforms.aliyun.login import AliyunLoginHandler
            handler = AliyunLoginHandler(browser_manager)
        elif platform == "glm":
            from platforms.glm.login import GLMLoginHandler
            handler = GLMLoginHandler(browser_manager)
        else:
            console.print(f"[red]Unknown platform: {platform}[/red]")
            return

        context = await handler.login()
        console.print(f"[green]Login successful for {platform}![/green]")
    finally:
        await browser_manager.close()


@cli.command()
@click.pass_context
def test_config(ctx):
    """Validate the configuration file."""
    config = ctx.obj["config"]
    console.print("[green]Configuration is valid![/green]")
    console.print(f"  Aliyun: {'enabled' if config.platforms.aliyun.enabled else 'disabled'} at {config.platforms.aliyun.purchase_time}")
    console.print(f"  GLM: {'enabled' if config.platforms.glm.enabled else 'disabled'} at {config.platforms.glm.purchase_time}")
    console.print(f"  GLM priority: {', '.join(config.platforms.glm.priority)}")
    console.print(f"  Browser headless: {config.browser.headless}")


@cli.command()
@click.pass_context
def list_platforms(ctx):
    """List available platforms and their status."""
    config = ctx.obj["config"]
    table = Table(title="Platform Status")
    table.add_column("Platform", style="cyan")
    table.add_column("Enabled", style="green")
    table.add_column("Purchase Time", style="yellow")
    table.add_column("Max Retries")
    table.add_column("Details")

    if config.platforms.aliyun.enabled:
        table.add_row("Aliyun", "Yes", config.platforms.aliyun.purchase_time,
                       str(config.platforms.aliyun.max_retries), "Coding Plan Pro")
    else:
        table.add_row("Aliyun", "No", "-", "-", "-")

    if config.platforms.glm.enabled:
        table.add_row("GLM", "Yes", config.platforms.glm.purchase_time,
                       str(config.platforms.glm.max_retries),
                       f"Priority: {', '.join(config.platforms.glm.priority)}")
    else:
        table.add_row("GLM", "No", "-", "-", "-")

    console.print(table)


@cli.command()
def generate_config():
    """Generate an example configuration file."""
    path = generate_example_config()
    console.print(f"[green]Example config generated at: {path}[/green]")


if __name__ == "__main__":
    cli()
