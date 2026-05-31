/**
 * @deprecated 此文件已废弃，实际 HTTP 同步由 dataStorage.ts 中的 syncToServer() 完成（端口 3000）。
 * 本文件硬编码端口 3456，无服务监听。保留仅供参考，请勿在新代码中导入。
 *
 * HTTP 同步服务（已废弃）
 * 将健康数据同步到 WSL 本地 HTTP 服务器
 */

import { HealthDataType } from '../types/healthData';

// WSL HTTP 服务器配置
const HTTP_SERVER_CONFIG = {
  host: 'http://127.0.0.1',
  port: 3456,
  timeout: 5000
};

class HttpSyncService {
  private serverUrl: string;
  private isServerAvailable: boolean = false;

  constructor() {
    this.serverUrl = `${HTTP_SERVER_CONFIG.host}:${HTTP_SERVER_CONFIG.port}`;
  }

  /**
   * 检查服务器是否可用
   */
  async checkServerStatus(): Promise<boolean> {
    return new Promise((resolve) => {
      wx.request({
        url: `${this.serverUrl}/health`,
        method: 'GET',
        timeout: HTTP_SERVER_CONFIG.timeout,
        success: (res) => {
          this.isServerAvailable = res.statusCode === 200;
          resolve(this.isServerAvailable);
        },
        fail: () => {
          this.isServerAvailable = false;
          resolve(false);
        }
      });
    });
  }

  /**
   * 同步数据到 WSL 服务器
   */
  async syncData(dataType: HealthDataType, data: any, date: string): Promise<boolean> {
    return new Promise((resolve) => {
      const payload = {
        dataType,
        date,
        data,
        timestamp: new Date().toISOString(),
        deviceInfo: this.getDeviceInfo()
      };

      wx.request({
        url: `${this.serverUrl}/api/health-data`,
        method: 'POST',
        header: {
          'Content-Type': 'application/json'
        },
        data: payload,
        timeout: HTTP_SERVER_CONFIG.timeout,
        success: (res) => {
          if (res.statusCode === 200 || res.statusCode === 201) {
            console.log(`[HttpSync] 数据同步成功: ${dataType}`);
            resolve(true);
          } else {
            console.warn(`[HttpSync] 同步失败，状态码: ${res.statusCode}`);
            resolve(false);
          }
        },
        fail: (err) => {
          console.warn(`[HttpSync] 同步失败:`, err);
          resolve(false);
        }
      });
    });
  }

  /**
   * 批量同步数据
   */
  async syncBatchData(dataList: Array<{dataType: HealthDataType, data: any, date: string}>): Promise<boolean> {
    return new Promise((resolve) => {
      const payload = {
        batch: dataList.map(item => ({
          ...item,
          timestamp: new Date().toISOString()
        })),
        deviceInfo: this.getDeviceInfo()
      };

      wx.request({
        url: `${this.serverUrl}/api/health-data/batch`,
        method: 'POST',
        header: {
          'Content-Type': 'application/json'
        },
        data: payload,
        timeout: HTTP_SERVER_CONFIG.timeout * 2,
        success: (res) => {
          if (res.statusCode === 200 || res.statusCode === 201) {
            console.log(`[HttpSync] 批量同步成功，共 ${dataList.length} 条数据`);
            resolve(true);
          } else {
            console.warn(`[HttpSync] 批量同步失败，状态码: ${res.statusCode}`);
            resolve(false);
          }
        },
        fail: (err) => {
          console.warn(`[HttpSync] 批量同步失败:`, err);
          resolve(false);
        }
      });
    });
  }

  /**
   * 获取设备信息
   */
  private getDeviceInfo(): object {
    try {
      const systemInfo = wx.getSystemInfoSync();
      return {
        brand: systemInfo.brand,
        model: systemInfo.model,
        platform: systemInfo.platform,
        system: systemInfo.system
      };
    } catch (e) {
      return {};
    }
  }

  /**
   * 获取服务器状态
   */
  getServerAvailable(): boolean {
    return this.isServerAvailable;
  }

  /**
   * 获取服务器 URL
   */
  getServerUrl(): string {
    return this.serverUrl;
  }
}

// 导出单例
export const httpSyncService = new HttpSyncService();
