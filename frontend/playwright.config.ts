import {defineConfig} from '@playwright/test';
import path from 'node:path';
const port=8850;
const python=process.platform==='win32'?'../.venv/Scripts/python.exe':'../.venv/bin/python';
const testData=process.env.STILLROOM_TEST_DATA_DIR||path.resolve('..','test-results',`e2e-data-${Date.now()}`);
export default defineConfig({
  testDir:'./e2e',timeout:60000,expect:{timeout:12000},workers:1,fullyParallel:false,
  reporter:'list',outputDir:'../test-results/browser',
  use:{baseURL:`http://127.0.0.1:${port}`,viewport:{width:1440,height:900},headless:true,trace:'retain-on-failure',screenshot:'only-on-failure',launchOptions:process.env.STILLROOM_BROWSER_CHANNEL?{channel:process.env.STILLROOM_BROWSER_CHANNEL}:{}},
  webServer:{command:`${python} ../run.py --port ${port} --data-dir "${testData}"`,url:`http://127.0.0.1:${port}/api/health`,reuseExistingServer:false,timeout:30000},
});
