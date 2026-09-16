import { httpRequest } from "@/lib/http";
import type { OperationalHealth } from "@/types/health";

export class ApiHealthService {
  async getDependencies(): Promise<OperationalHealth> {
    return httpRequest<OperationalHealth>("/health/dependencies");
  }
}
